"""Minimal FusionSolar client used by the MQTT bridge."""

import logging
import time
from functools import wraps
import json
from typing import Optional
import re
import requests
from urllib.parse import urlparse

from .exceptions import (
    AuthenticationException,
    FusionSolarException,
)
from .encryption import encrypt_password, get_secure_random
from .devices import plant_api

# global logger object
_LOGGER = logging.getLogger(__name__)

REQUEST_TIMEOUT = (10, 30)


def _redirect_hostname(url: str) -> str | None:
    try:
        return (urlparse(url).hostname or "").lower() or None
    except (TypeError, ValueError):
        return None


def _is_allowed_fusionsolar_redirect(url: str) -> bool:
    hostname = _redirect_hostname(url)

    return bool(
        hostname
        and (
            hostname == "fusionsolar.huawei.com"
            or hostname.endswith(".fusionsolar.huawei.com")
        )
    )


class TimeoutSession(requests.Session):
    """Requests session with a default connect and read timeout."""

    def __init__(self, timeout: tuple[int, int] = REQUEST_TIMEOUT) -> None:
        super().__init__()
        self._timeout = timeout

    def request(self, *args, **kwargs):
        kwargs.setdefault("timeout", self._timeout)
        return super().request(*args, **kwargs)

def logged_in(func):
    """
    Decorator to make sure user is logged in.
    """

    @wraps(func)
    def wrapper(self, *args, **kwargs):
        # use the is-session-alive feature to check whether the session is active
        if not self.is_session_active():
            _LOGGER.debug("No active session. Resetting session and logging in...")

            # reset the session
            self._session = self._new_session()
            self._configure_session()

        try:
            result = func(self, *args, **kwargs)
        except json.JSONDecodeError:
            # this may indicate that the login failed
            _LOGGER.error("Login apparently failed. Received invalid response.")
            raise FusionSolarException("Failed to reset session and login again.")

        return result

    return wrapper


class FusionSolarClient:
    """The main client to interact with the Fusion Solar API"""

    _LOGGER = _LOGGER

    def __init__(
        self,
        username: str,
        password: str,
        huawei_subdomain: str = "region01eu5",
        session: Optional[requests.Session] = None,
    ) -> None:
        """Initializes a new FusionSolarClient instance. This is the main
           class to interact with the FusionSolar API.
           The client tests the login credentials as soon as it is initialized
        :param username: The username for the system
        :type username: str
        :param password: The password
        :type password: str
        :param huawei_subdomain: The FusionSolar API uses different subdomains for different regions.
                                 Adapt this based on the first part of the URL when you access your system.
        :type huawei_subdomain: str
        :param session: An optional requests session object. If not set, a new session will be created.
        :type session: requests.Session
        """
        self._user = username
        self._password = password
        if session is None:
            self._session = self._new_session()
        else:
            self._session = session

        suffix = ".fusionsolar.huawei.com"

        while huawei_subdomain.endswith(suffix):
            huawei_subdomain = huawei_subdomain[: -len(suffix)]

        self._huawei_subdomain = huawei_subdomain

        if self._huawei_subdomain.startswith("region"):
            self._login_subdomain = self._huawei_subdomain[8:]
        elif self._huawei_subdomain.startswith("uni"):
            self._login_subdomain = self._huawei_subdomain[6:]
        else:
            self._login_subdomain = self._huawei_subdomain

        # Only login if no session has been provided. The session should hold the cookies for a logged in state
        if session is None:
            self._configure_session()

    @staticmethod
    def _new_session() -> TimeoutSession:
        return TimeoutSession()

    def _is_intl_subdomain(self) -> bool:
        """Check if this is the INTL subdomain which uses a different API."""
        return self._huawei_subdomain in ["intl", "la5"]

    def _login_intl(self):
        """Login flow for the INTL subdomain which uses a different API."""
        _LOGGER.debug(f"Using INTL login flow for subdomain: {self._huawei_subdomain}")

        url = f"https://{self._huawei_subdomain}.fusionsolar.huawei.com/rest/dp/uidm/unisso/v1/validate-user"
        url_params = {"service": "/"}

        json_data = {
            "username": self._user,
            "password": self._password,
        }

        headers = {"App-Id": "smartpvms"}

        r = self._session.post(
            url=url, params=url_params, json=json_data, headers=headers
        )
        r.raise_for_status()

        try:
            login_response = r.json()
        except Exception as e:
            _LOGGER.error(
                f"Retrieved invalid data as login response for {self._huawei_subdomain}."
            )
            _LOGGER.exception(e)
            raise FusionSolarException(
                f"Failed to process login response for {self._huawei_subdomain}"
            )

        # INTL uses "code" instead of "errorCode"
        if login_response.get("code") != 0:
            error_msg = login_response.get("payload", {}).get(
                "exceptionMessage", "Unknown error"
            )
            raise AuthenticationException(
                f"Failed to login into FusionSolarAPI ({self._huawei_subdomain}): {error_msg}"
            )

        # Handle the redirect URL from the response
        payload = login_response.get("payload", {})
        redirect_url = payload.get("redirectURL")
        if redirect_url:
            if not isinstance(redirect_url, str):
                _LOGGER.error("Unexpected FusionSolar redirect host: <missing>")
                raise FusionSolarException("Unexpected FusionSolar redirect host")

            # If redirect URL is relative, prepend the base URL
            if redirect_url.startswith("/"):
                redirect_url = f"https://{self._huawei_subdomain}.fusionsolar.huawei.com{redirect_url}"

            if not _is_allowed_fusionsolar_redirect(redirect_url):
                hostname = _redirect_hostname(redirect_url) or "<missing>"
                _LOGGER.error("Unexpected FusionSolar redirect host: %s", hostname)
                raise FusionSolarException("Unexpected FusionSolar redirect host")

            parsed_redirect = urlparse(redirect_url)
            _LOGGER.debug(
                "Following FusionSolar redirect: %s://%s%s",
                parsed_redirect.scheme,
                parsed_redirect.hostname,
                parsed_redirect.path,
            )
            # Don't follow redirects - we just need the cookies from the first response
            # The final redirect may go to an internal domain that's not publicly accessible
            redirect_response = self._session.get(redirect_url, allow_redirects=False)
            # Accept 302 as success - it means the SSO ticket was accepted
            if redirect_response.status_code not in (200, 302):
                redirect_response.raise_for_status()

    def _login(self):
        # Use different login flow for INTL subdomain
        if self._is_intl_subdomain():
            return self._login_intl()

        # retrieve the public key in order to test which loging function to use
        key_request = self._session.get(
            f"https://{self._login_subdomain}.fusionsolar.huawei.com/unisso/pubkey"
        )

        if key_request.status_code != 200:
            _LOGGER.error(
                f"Failed to retrieve public key. Status code = {key_request.status_code}"
            )
            raise FusionSolarException("Failed to retrieve public key.")

        key_data = key_request.json()

        # find the correct login function
        url = f"https://{self._login_subdomain}.fusionsolar.huawei.com/unisso/v2/validateUser.action"
        url_params = {}
        password = self._password

        if key_data["enableEncrypt"]:
            _LOGGER.debug("Using V3 loging function with encrypted passwords")
            url = f"https://{self._login_subdomain}.fusionsolar.huawei.com/unisso/v3/validateUser.action"
            _LOGGER.debug(url)
            url_params["timeStamp"] = key_data["timeStamp"]
            url_params["nonce"] = get_secure_random()

            # encrypt the password
            password = encrypt_password(key_data=key_data, password=password)
        else:
            url_params["decision"] = 1
            url_params["service"] = (
                f"https://{self._huawei_subdomain}.fusionsolar.huawei.com/unisess/v1/auth?service=/netecowebext/home/index.html#/LOGIN",
            )

        json_data = {
            "organizationName": "",
            "username": self._user,
            "password": password,
        }

        # send the request
        r = self._session.post(url=url, params=url_params, json=json_data)
        r.raise_for_status()

        try:
            login_response = r.json()
        except Exception as e:
            _LOGGER.error("Retrieved invalid data as login response.")
            _LOGGER.debug(r.json())
            _LOGGER.exception(e)
            raise FusionSolarException("Failed to process login response")

        # in the new login procedure, an errorCode 470 is pointing to a success
        # but requires another request to start the session
        if login_response["errorCode"] == "470":
            resp_multi = login_response.get("respMultiRegionName")
            if not isinstance(resp_multi[1], list) and not resp_multi[1].startswith(
                "/"
            ):
                self.MultiRegionName = self._huawei_subdomain
                # If subdomain end with eu5 e.g. region01eu5 remove the eu5 part
                if self._huawei_subdomain.endswith("eu5"):
                    self.MultiRegionName = self._huawei_subdomain[:-3]

                if (
                    self.MultiRegionName.startswith("region")
                    and not self.MultiRegionName == "region05"
                ):
                    pattern = r"(region0?)(\d{1,2})"

                    def repl(match):
                        prefix, num = match.groups()
                        return f"region{int(num):03d}"

                    self.MultiRegionName = re.sub(pattern, repl, self.MultiRegionName)

                key_data = key_request.json()

                # find the correct login function
                url = f"https://{self._login_subdomain}.fusionsolar.huawei.com/unisso/v2/validateUser.action"
                url_params = {}
                password = self._password

                if key_data["enableEncrypt"]:
                    _LOGGER.debug("Using V3 loging function with encrypted passwords")
                    url = f"https://{self._login_subdomain}.fusionsolar.huawei.com/unisso/v3/validateUser.action"
                    _LOGGER.debug(url)
                    url_params["timeStamp"] = key_data["timeStamp"]
                    url_params["nonce"] = get_secure_random()

                    # encrypt the password
                    password = encrypt_password(key_data=key_data, password=password)

                json_data = {
                    "organizationName": "",
                    "username": self._user,
                    "password": password,
                    "multiRegionName": self.MultiRegionName,
                }

                # send the request
                r = self._session.post(url=url, params=url_params, json=json_data)
                r.raise_for_status()
                login_response = r.json()

            _LOGGER.debug("New loging procedure successful, sending additional request")
            target_subdomain = login_response["respMultiRegionName"][1]
            target_url = f"https://{self._login_subdomain}.fusionsolar.huawei.com{target_subdomain}"

            new_procedure_response = self._session.get(target_url)
            new_procedure_response.raise_for_status()

        # make sure that the login worked - NOTE: This may no longer work with the new procedure
        error = None
        if login_response["errorMsg"]:
            error = login_response["errorMsg"]

        if error:
            raise AuthenticationException(
                f"Failed to login into FusionSolarAPI: {error}"
            )

    def _configure_session(self):
        """Logs into the Fusion Solar API. Raises an exception if the login fails."""
        # check the login credentials right away
        _LOGGER.debug("Logging into Huawei Fusion Solar API")

        # set the user agent
        self._session.headers["User-Agent"] = (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0.0.0 Safari/537.36"
        )

        self._login()

        # get the payload
        payload = self.keep_alive()

        if not payload:
            raise FusionSolarException(
                "Login failed. No payload received from keep-alive."
            )

        # get the main id
        r = self._session.get(
            url=f"https://{self._huawei_subdomain}.fusionsolar.huawei.com/rest/neteco/web/organization/v2/company/current",
            params={"_": round(time.time() * 1000)},
        )

        # the new API returns a 500 exception if the subdomain is incorrect
        if r.status_code == 500 or r.status_code == 400:
            try:
                data = r.json()

                if (
                    data["exceptionId"] == "Query company failed."
                    or data["exceptionId"] == "bad status"
                ):
                    raise AuthenticationException(
                        "Invalid response received. Please check the correct Huawei subdomain."
                    )
            except (json.JSONDecodeError, requests.exceptions.HTTPError) as e:
                _LOGGER.error("Login validation failed. Failed to process response.")
                _LOGGER.exception(e)
                raise AuthenticationException("Failed to log into FusionSolarAPI.")

        r.raise_for_status()

        # catch an incorrect subdomain
        response_text = r.content.decode()

        if not response_text.strip().startswith('{"data":'):
            raise AuthenticationException(
                "Invalid response received. Please check the correct Huawei subdomain."
            )

        response_data = r.json()

        if "data" not in response_data:
            _LOGGER.error(
                f"Failed to retrieve data object. {json.dumps(response_data)}"
            )
            raise AuthenticationException("Failed to login into FusionSolarAPI.")

        self._company_id = r.json()["data"]["moDn"]

    def is_session_active(self) -> bool:
        """Tests whether the current session is active. In the web-based application, this
        function is triggered every 10 seconds.

        :return: Indicates whether the current session is active.
        :rtype: bool
        """
        if not self._session:
            return False

        try:
            r = self._session.get(
                f"https://{self._huawei_subdomain}.fusionsolar.huawei.com/rest/dpcloud/auth/v1/is-session-alive"
            )
            r.raise_for_status()
            response_data = r.json()
        except (requests.exceptions.RequestException, ValueError):
            _LOGGER.debug(
                "FusionSolar session check failed; treating session as inactive"
            )
            return False

        return isinstance(response_data, dict) and response_data.get("code") == 0

    @logged_in
    def keep_alive(self):
        """This function replicates a call sent by the web-based application. Currently,
        the rate at which this function is called is unclear. It seems to be called around
        every 30 seconds.

        :return: This function returns the payload returned by the respective call
        :rtype: str
        """
        r = self._session.get(
            f"https://{self._huawei_subdomain}.fusionsolar.huawei.com/rest/dpcloud/auth/v1/keep-alive"
        )
        r.raise_for_status()

        response_data = r.json()

        if "code" not in response_data or response_data["code"] != 0:
            raise FusionSolarException("Failed to set keep alive.")

        # get the payload
        if "payload" in response_data:
            # save the payload as a session header
            self._session.headers["roarand"] = response_data["payload"]
            return response_data["payload"]

        return None

    @logged_in
    def get_current_plant_data(self, plant_id: str) -> dict:
        return plant_api.get_current_plant_data(self, plant_id)

    @logged_in
    def get_station_list(self) -> list:
        return plant_api.get_station_list(self)
