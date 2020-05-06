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
piapi module stands for (Cisco) Prime Infrastructure API.
The module implements the PIAPI class which helps interacting with the Cisco Prime Infrastructure REST API using
simple methods that can either request data or request an action.

The Cisco Prime Infrastructure API is a REST API which exposes several resources that can be of 2 types:
    * Data resources: expose some data collected by the software which can be retrieved (e.g: client summary).
    * Service resources: expose some services that can modify the configuration of the software (e.g: modify/update an Access Point)

The REST API is applying request rate limiting to avoid server's overloading. To bypass this limitation, especially
when requesting data resources, the PIAPI uses multithreading requests (grequests library) with an hold time between
chunk of requests. Please check the documentation to knowns more about rate limiting.

Also note that the piapi module only works with the JSON structure exposed by the REST API. The module doesn't support
the default XML structure.

Please check your Cisco Prime REST API available at http://{server-name}/webacs/api/v1/
"""

from __future__ import absolute_import
import six.moves.urllib.parse
import time
import copy
import hashlib
import threading
import six.moves.queue
import json

import requests
import requests.auth
from six.moves import range

#import grequests

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
Default time in second to wait for a response fomr the REST API
"""
DEFAULT_REQUEST_TIMEOUT = 300
"""
Default base URI of the Prime API
"""
DEFAULT_API_URI = "/webacs/api/v3/"


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
        The base URL to get access to Cisco Prime Infrastructure (without the URI of the REST API!).
    username : str
        Username to be used for authentication.
    password : str
        Password to be used for authentication.
    verify : bool (optional)
        Whether or not to verify the server's SSL certificate (default: True).
    virtual_domain : str (optional)
        The virtual domain used by all the request. Virtual domain are used as a filter (default: None).
    """

    def __init__(self, url, username, password, verify=True, virtual_domain=None):
        """
        Constructor of the PIAPI class.
        """
        self.base_url = six.moves.urllib.parse.urljoin(url, DEFAULT_API_URI)
        self.verify = verify
        self.virtual_domain = virtual_domain
        self.cache = {}  # Caching is used for data resource with keys as checksum of resource's name+params from the request

        # Service resources holds all possible service resources with keys as service name
        # and hold the HTTP method + full url to request the service.
        self._service_resources = {}
        # Data resources holds all possible data resources with key as service name and value as full url access.
        self._data_resources = {}

        self.session = requests.Session()
        self.session.auth = requests.auth.HTTPBasicAuth(username, password)

        # Disable HTTP keep_alive as advised by the API documentation
        self.session.headers['connection'] = 'close'

        # Don't print warning message from request if not wanted
        if not self.verify:
            import warnings
            warnings.filterwarnings("ignore")

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
            return response_json
        elif response.status_code == 302:
            raise PIAPIRequestError("Incorrect credentials provided")
        elif response.status_code == 400:
            response_json = response.json()
            raise PIAPIRequestError("Invalid request: %s" % response_json["errorDocument"]["message"])
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

    def _request_wrapper(self, queue, url, params, timeout):
        """
        Wrapper to requests used by each thread.

        Parameters
        ----------
        queue : Queue.Queue
            The Queue to write the response from the request in.
        url : str
            The URL to be queried.
        params : dict
            A dictionary of parameters to pass to the request.
        timeout : int
            Timeout to wait for a response to the request.
        """
        response = self.session.get(url, params=params, verify=self.verify, timeout=timeout)
        queue.put(response)

    @property
    def resources(self):
        """
        List of all available resources to be requested. This includes actions and data resources.
        """
        return self.data_resources + self.service_resources

    @property
    def data_resources(self):
        """
        List of all available data resources, meaning resources that return data.
        """
        if self._data_resources:
            return list(self._data_resources.keys())

        data_resources_url = six.moves.urllib.parse.urljoin(self.base_url, "data.json")
        response = self.session.get(data_resources_url, verify=self.verify)
        response_json = self._parse(response)
        for entry in response_json["queryResponse"]["entityType"]:
            self._data_resources[entry["$"]] = "%s.json" % entry["@url"]

        return list(self._data_resources.keys())

    @property
    def service_resources(self):
        """
        List of all available service resources, meaning resources that modify the NMS.
        """
        if self._service_resources:
            return list(self._service_resources.keys())

        service_resources_url = six.moves.urllib.parse.urljoin(self.base_url, "op.json")
        response = self.session.get(service_resources_url, verify=self.verify)
        response_json = self._parse(response)
        for entry in response_json["queryResponse"]["operation"]:
            self._service_resources[entry["$"]] = {"method": entry["@httpMethod"], "url": six.moves.urllib.parse.urljoin(self.base_url, "op/%s.json" % entry["@path"])}

        return list(self._service_resources.keys())

    def request_data(self, resource_name, params={}, check_cache=True, timeout=DEFAULT_REQUEST_TIMEOUT, paging_size=DEFAULT_PAGE_SIZE, concurrent_requests=DEFAULT_CONCURRENT_REQUEST, hold=DEFAULT_HOLD_TIME):
        """
        Request a 'resource_name' resource from the REST API. The request can be tuned with filtering, sorting options.
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
        timeout : int (optional)
            Time to wait for a response from the REST API (default : piapi.DEFAULT_REQUEST_TIMEOUT)
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
        #  hash_cache = hashlib.sha256(b"%s%s" % (resource_name, params)).hexdigest()
        #   if check_cache and hash_cache in self.cache:
        #      return self.cache[hash_cache]

        #  Get total number of entries for the request
        response = self.session.get(self._data_resources[resource_name], params=params, timeout=timeout)
        self._parse(response)
        count_entry = int(response.json()["queryResponse"]["@count"])
        if count_entry <= 0:
            raise PIAPICountError("No result found for the query %s with params %s" % (response.url, params))

        #  Create the necessary requests with paging to avoid rate limiting
        paging_requests = []
        queue = six.moves.queue.Queue()
        for first_result in range(0, count_entry, paging_size):
            params_copy = copy.deepcopy(params)
            params_copy.update({".full": "true", ".firstResult": first_result, ".maxResults": paging_size})
            #paging_requests.append(grequests.get(self._data_resources[resource_name], session=self.session, params=params_copy, verify=self.verify, timeout=timeout))
            paging_requests.append(threading.Thread(None, self._request_wrapper, args=(queue,
                                                                                       self._data_resources[resource_name],
                                                                                       params_copy,
                                                                                       timeout)))

        #  Create chunks from the previous list of requests to avoid rate limiting (we hold between each chunk)
        chunk_requests = [paging_requests[x:x+concurrent_requests] for x in range(0, len(paging_requests), concurrent_requests)]

        #  Bulk query the chunk pages by waiting between each chunk to avoid rate limiting
        responses = []
        for chunk_request in chunk_requests:
            #responses += grequests.map(chunk_request)
            for request in chunk_request:
                request.start()
            for request in chunk_request:
                request.join()
                responses.append(queue.get())
            time.sleep(hold)

        #  Parse the results of the previous queries
        results = []
        for response in responses:
            response_json = self._parse(response)
            results += response_json["queryResponse"]["entity"]
        #  self.cache[hash_cache] = results
        return results

    def request_service(self, resource_name, params=None, timeout=DEFAULT_REQUEST_TIMEOUT):
        """
        Request a service resource from the REST API.

        Parameters
        ----------
        resource_name : str
            Action resource to be requested
        params : dict (optional)
            JSON parameters to be sent along the resource_name request (default : empty dict)
        timeout : int (optional)
            Time to wait for a response from the REST API (default : piapi.DEFAULT_REQUEST_TIMEOUT)

        Returns
        -------
        results : JSON structure
            Data results from the requested resources.
        """
        if resource_name not in self.service_resources:
            raise PIAPIResourceNotFound("Service Resource '%s' not found in the API, check 'service_resources' property "
                                        "for a list of available actions" % resource_name)

        method = self._service_resources[resource_name]["method"]
        url = self._service_resources[resource_name]["url"]
        headers = {'Content-Type':'application/json'}
        # if the HTTP method is 'GET', use the params args of request, otherwise use data (POST, DELETE, PUT)
        if method == "GET":
            response = self.session.request(method, url, params=params, verify=self.verify, timeout=timeout)
        elif method == "PUT" or method == "POST":
            response = self.session.request(method, url, data=json.dumps(params), headers=headers, verify=self.verify, timeout=timeout)
        else:
            response = self.session.request(method, url, data=params, verify=self.verify, timeout=timeout)
        return self._parse(response)

    def request(self, resource, params={}, virtual_domain=None, check_cache=True, timeout=DEFAULT_REQUEST_TIMEOUT, paging_size=DEFAULT_PAGE_SIZE,
                concurrent_requests=DEFAULT_CONCURRENT_REQUEST, hold=DEFAULT_HOLD_TIME):
        """
        Generic request for either data or services resources. The parameters correspond to the ones from
        *PIAPI.request_data* or *PIAPI.request_action*.

        Parameters
        ----------
        resource : str
            Action resource to be requested
        params : dict (optional)
            JSON parameters to be sent along the resource_name request (default : empty dict).
        virtual_domain : str (optional)
            Name of the virtual domain to send the request for (default : None).
        check_cache : bool (optional)
            Whether or not to check the cache instead of performing a call against the REST API.
        timeout : int (optional)
            Time to wait for a response from the REST API (default : piapi.DEFAULT_REQUEST_TIMEOUT)
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
        virtual_domain = virtual_domain or self.virtual_domain
        if virtual_domain:
            params["_ctx.domain"] = virtual_domain

        if resource in self.data_resources:
            return self.request_data(resource, params, check_cache, timeout, paging_size, concurrent_requests, hold)
        elif resource in self.service_resources:
            return self.request_service(resource, params, timeout)

    def __getattr__(self, item):
        """
        Magic method used to render all resources as class attribute

        item : str
            Name of the resource to be found
        """
        if item in self.resources:
            return self.request(item)
        raise AttributeError("'%s' resource not found in the REST API" % item)

