#   Copyright 2015 maximumG
#
#   Licensed under the Apache License, Version 2.0 (the "License");
#   you may not use this file except in compliance with the License.
#   You may obtain a copy of the License at
#
#       http://www.apache.org/licenses/LICENSE-2.0
#
#   Unless required by applicable law or agreed to in writing, software
#   distributed under the License is distributed on an "AS IS" BASIS,
#   WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
#   See the License for the specific language governing permissions and
#   limitations under the License.
"""
piapi module stands for (Cisco) **P**rime **I**nfrastructure **API**.
The module implements the PIAPI class which helps interacting with the Cisco Prime Infrastructure REST API using
simple methods that can either request data or request an action.

The Cisco Prime Infrastructure API is a REST API which exposes several resources that can be of 2 types:
* **Data** resources: expose some data collected by the software which can be retrieved (e.g: client summary).
* **Action** resources: expose some action that can modify the configuration of the software (e.g: modify/update an Access Point)

The REST API is applying request rate limiting to avoid server's overloading. To bypass this limitation, especially
when requesting data resources, the PIAPI uses multithreading requests (grequests library) with an hold time between
chunk of requests. Please check the documentation to knowns more about rate limiting.

Also note that the piapi module only works with the JSON structure exposed by the REST API. The module doesn't support
the default XML structure.

Please check your Cisco Prime REST API available at http://{server-name}/webacs/api/v1/
"""

import urlparse
import collections
import time
import copy
import hashlib

import requests
import requests.auth
import grequests

"""
Default number of concurrent requests (check *Rate Limiting* of the API)
"""
DEFAULT_CONCURRENT_REQUEST = 5
"""
Default number of results per page (check *Rate Limiting* of the API)
"""
DEFAULT_PAGE_SIZE = 1000
"""
Default hold time in second to wait between group of concurrent request to avoid rate timiting (check *Rate Limiting* of the API)
"""
DEFAULT_HOLD_TIME = 1
"""
Default base URI of the Prime API
"""
DEFAULT_API_URI = "/webacs/api/v1/"


class PIAPIError(Exception):
    """
    Generic error raised by the piapi module.
    """


class PIAPIRequestError(PIAPIError):
    """
    Error raised by the piapi module when HTTP error code occurred.
    """


class PIAPICountError(PIAPIError):
    """
    Error raised by the piapi module when no result can be found for an API request.
    """


class PIAPIResourceNotFound(PIAPIError):
    """
    Error raised by the piapi module when a requested resource is not available in the API.
    """


class PIAPI(object):
    """
    Interface with the Cisco Prime Infrastructure REST API.

    Attributes
    ----------
    base_url : str
        The base URL to get access to the API (e.g. https://{server}/webacs/v1/api/).
    verify : bool
        Whether or not to verify the server's SSL certificate.
    cache : dict
        Cache for all data requests already performed.
    session : requests.Session
        HTTP session that will be used as base for all interaction with the REST API.

    Parameters
    ----------
    url : str
        The base URL to get access to Cisco Prime Infrastructure (without the URI of the REST API!)
    username : str
        Username to be used for authentication
    password : str
        Password to be used for authentication
    verify : bool (optional)
        Whether or not to verify the server's SSL certificate (default: True)
    """

    def __init__(self, url, username, password, verify=True):
        """
        Constructor of the PIAPI class.
        """
        self.base_url = urlparse.urljoin(url, DEFAULT_API_URI)
        self.verify = verify
        self.cache = {}  # Caching is used for data resource with keys as checksum of resource's name+params from the request

        # Action resources holds all possible service resources with keys as service name
        # and hold the HTTP method + full url to request the service.
        self._action_resources = collections.defaultdict(default_factory=lambda: {"method": None, "url": None})
        # Data resources holds all possible data resources with key as service name and value as full url access.
        self._data_resources = {}

        self.session = requests.Session()
        self.session.auth = requests.auth.HTTPBasicAuth(username, password)
        self.session.keep_alive = False  # Disable HTTP keep_alive as advised by the API documentation

    def _parse(self, response):
        """
        Parse a requests.Response object to check for potential errors using the HTTP status code.
        Please check your Cisco Prime Infrastructure REST API documentation for errors and return code.

        Parameters
        ----------
        response : requests.Response
            HTTP response from an HTTP requests.

        Returns
        -------
        response_json : JSON structure
            The JSON structure from the response.
        """
        if response.status_code == 200:
            response_json = response.json()
            if "QueryResponse" in response_json and response_json["QueryResponse"]["count"] == 0:
                raise PIAPICountError("No result found for the query %s" % response.url)
            return response_json
        elif response.status_code == 302:
            raise PIAPIRequestError("Incorrect credentials provided")
        elif response.status_code == 400:
            raise PIAPIRequestError("Invalid request %s" % response.url)
        elif response.status_code == 401:
            raise PIAPIRequestError("Unauthorized access")
        elif response.status_code == 403:
            raise PIAPIRequestError("Forbidden access to the REST API")
        elif response.status_code == 404:
            raise PIAPIRequestError("URL not found %s" % response.url)
        elif response.status_code == 406:
            raise PIAPIRequestError("The Accept header sent in the request does not match a supported type")
        elif response.status_code == 415:
            raise PIAPIRequestError("The Content-Type header sent in the request does not match a supported type")
        elif response.status_code == 500:
            raise PIAPIRequestError("An error has occured during the API invocation")
        elif response.status_code == 502:
            raise PIAPIRequestError("The server is down or being upgraded")
        elif response.status_code == 503:
            raise PIAPIRequestError("The servers are up, but overloaded with requests. Try again later (rate limiting)")
        else:
            raise PIAPIRequestError("Unknown Request Error, return code is %s" % response.status_code)

    @property
    def resources(self):
        """
        List of all available resources to be requested. This includes actions and data resources.
        """
        return self.data_resources + self.action_resources

    @property
    def data_resources(self):
        """
        List of all available data resources, meaning resources that return data.
        """
        if self._data_resources:
            return self._data_resources.keys()

        data_resources_url = urlparse.urljoin(self.base_url, "data/.json")
        response = self.session.get(data_resources_url)
        response_json = self._parse(response)
        #  TODO : parse the structure as JSON

    @property
    def action_resources(self):
        """
        List of all available action resources, meaning management actions.
        """
        if self._action_resources:
            return self._action_resources.keys()

        action_resources_url = urlparse.urljoin(self.base_url, "op.json")
        response = self.session.get(action_resources_url)
        response_json = self._parse(response)
        #  TODO : parse the structure as JSON

    def request_data(self, resource_name, params={}, check_cache=True, paging_size=DEFAULT_PAGE_SIZE, concurrent_requests=DEFAULT_CONCURRENT_REQUEST, hold=DEFAULT_HOLD_TIME):
        """
        Request a resource_name resource from the REST API. The request can be tuned with filtering, sorting options.
        Check the REST API documentation for available filters by resource.

        To bypass rate limiting feature of the API you can tune paging_size, concurrent_requests and hold_time parameters.
        'X' concurrent requests will be sent as chunk and we will wait the hold time before sending the next chunk until
        all resource_name have been retrieved.

        Parameters
        ----------
        resource_name : str
            Data resource name to be requested.
        params : dict (optional)
            Additional parameters to be sent along the query for filtering, sorting,... (default : empty dict).
        check_cache : bool (optional)
            Whether or not to check the cache instead of performing a call against the REST API.
        paging_size : int (optional)
            Number of entries to include per page (default : piapi.DEFAULT_PAGE_SIZE).
        concurrent_requests : int (optional)
            Number of parallel requests to make (default : piapi.DEFAULT_CONCURRENT_REQUEST).
        hold : int (optional)
            Hold time in second to wait between chunk of concurrent requests to avoid rate limiting (default : piapi.DEFAULT_HOLD_TIME).

        Returns
        -------
        results : JSON structure
            Data results from the requested resources.
        """
        if resource_name not in self.data_resources:
            raise PIAPIResourceNotFound("Data Resource '%s' not found in the API, check 'data_resources' property "
                                        "for a list of available resource_name" % resource_name)

        #  Check the cache to see if the couple (resource + parameters) already exists (using SHA256 hash of resource_name and params)
        hash_cache = hashlib.sha256(b"%s%s" % (resource_name, str(params))).hexdisgest()
        if check_cache and hash_cache in self.cache:
            return self.cache[hash_cache]

        #  Get total number of entries for the request
        response = self.session.get(self._api_structure[resource_name]["url"], params=params)
        self._parse(response)
        count_entry = response.json()["QueryResponse"]["@counts"]

        #  Create the necessary requests with paging to avoid rate limiting
        paging_requests = []
        for first_result in range(0, count_entry, paging_size):
            params_copy = copy.deepcopy(params)
            params_copy.update({".full": "true", "firstResult": first_result, "maxResults": paging_size})
            paging_requests.append(grequests.get(self._data_resources[resource_name], session=self.session, params=params_copy, verify=self.verify))

        #  Create chunks from the previous list of requests to avoid rate limiting (we hold between each chunk)
        chunk_requests = [paging_requests[x:x+concurrent_requests] for x in range(0, len(paging_requests), concurrent_requests)]

        #  Bulk query the chunk pages by waiting between each chunk to avoid rate limiting
        responses = []
        for chunk_request in chunk_requests:
            responses.append(grequests.map(chunk_request))
            time.sleep(hold)

        #  Parse the results of the previous queries
        results = []
        for response in responses:
            response_json = self._parse(response)
            results.append(response_json["QueryResponse"])
        self.cache[hash_cache] = results
        return results

    def request_action(self, resource_name, payload=None):
        """
        Request an resource_name resource from the REST API.

        Parameters
        ----------
        resource_name : str
            Action resource to be requested
        payload : dict (optional)
            JSON payload to be sent along the resource_name request (default : empty dict)

        Returns
        -------
        results : JSON structure
            Data results from the requested resources.
        """
        if resource_name not in self.action_resources:
            raise PIAPIResourceNotFound("Action Resource '%s' not found in the API, check 'action_resources' property "
                                        "for a list of available actions" % resource_name)

        method = self._action_resources[resource_name]["method"]
        url = self._action_resources[resource_name]["url"]
        response = self.session.request(method, url, data=payload, verify=self.verify)
        return self._parse(response)

    def request(self, resource, data=None, params=None, check_cache=True, paging_size=DEFAULT_PAGE_SIZE,
                concurrent_requests=DEFAULT_CONCURRENT_REQUEST, hold=DEFAULT_HOLD_TIME):
        """
        Generic request which for either data or action resources. The parameters correspond to the ones from
        *PIAPI.request_data* or *PIAPI.request_action*.

        Returns
        -------
        results : JSON structure
            Data results from the requested resources.
        """
        if resource in self._data_resources:
            return self.request_data(resource, params, check_cache, paging_size, concurrent_requests, hold)
        elif resource in self._action_resources:
            return self.request_action(resource, data)

