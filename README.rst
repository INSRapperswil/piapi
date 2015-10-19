piapi AKA Prime Infrastructure API
==================================

The piapi library ease the interaction with the Cisco Prime Infrastructure REST API with python.
piapi implements a unique class known as **PIAPI** has the unique entry point for all requests made against the API.

Installation
------------

::

    pip install piapi

or ::

    pip install https://github.com/maximumG/piapi/archive/master.zip


Cisco Prime Infrastructure REST API
===================================

Prime Infrastructure Network Monitoring/Configuration solution exposes a classic REST API to get
access to several *resource*. The resources are of 2 types:

* **Data** resources: exposes several statistics/metric of the network and often requested using HTTP GET (e.g. client summary, alarms,...)
* **Action** resources: exposes several services to modify the configuration of the NMS/network and often used with HTTP POST, PUT and DELETE

Check the Cisco Prime Infrastructure REST API documentation available at https://{pi-server}/webacs/api/v1/

Note that piapi library is only interacting with the REST API using JSON structure.

How does piapi works ?
======================

Basic Usage
-----------

The following code resumes all functionalities of the *PIAPI* class.

::

    from piapi import PIAPI
    
    api = PIAPI("https://pi-server/", "username" , "password")
    
    api.resources
    api.data_resources
    api.action_resources
    
    #  Request a Data resource from the API
    api.request("Clients", params={"connectionType": "LIGHTWEIGHTWIRELESS"})
    
    #  Request a Action resource from the API
    to_delete = {"deviceDeleteCandidates": {"ipAddresses": {"ipAddress": "1.1.1.1"}}}
    api.request("deleteDevices", data=to_delete)

We can request several properties from the class such as *resources*, *data_resources*, *action_resources*.
These properties are list of available resources that are exposed from the REST API.
The resources can used after when calling the API with the *request* method.

The *request* method is the generic entry point to interact with the REST API. It needs to be called using the resource's
name as required argument and some *params* (data requests) or *payload* (action requests). All requests will return the response
as JSON structure.

Also note that the requests for data resource always returns a detailed JSON structure and not the summary one.

Rate Limiting
-------------

The Cisco Prime API is using rate limiting features to protect the server from request's overloading. This means that
the API is restricting requests from a user inside a window of time.

To 'bypass' and embrace this rate limiting feature, the *request* method can be tuned using optional parameters:

* **paging_size**: the maximum result that can be present in a page (default : 1000)
* **concurrent_requests**: the number of requests that can be sent in parallel (default : 5)
* **hold**: time in second to wait between each chunk of concurrent request (default : 1)

Check the Cisco Prime Infrastructure REST API documentation to known more about the rate limiting feature and how
it can be tuned internally.

PIAPI Caching feature
---------------------

Some REST API call can be extensively long, depending on what you want to retrieve. For that matter, the PIAPI class implements
a caching mechanism for data resources only. When calling the request method for the same couple resource+parameters more
than once, the method will return the already stored result instead of running the request again.

::

    api = PIAPI("https://pi-server/", "username" , "password")

    # This request will be run against the REST API (could take a long time)
    api.request("Clients", params={"connectionType": "LIGHTWEIGHTWIRELESS"})

    # This request will comes directly from the cache PIAPI class (faster return of data)
    api.request("Clients", params={"connectionType": "LIGHTWEIGHTWIRELESS"})

You can explicitly avoid using the cache by setting the *check_cache* argument to *False* in the request method.

::

    api.request("Clients", params={"connectionType": "LIGHTWEIGHTWIRELESS"}, check_cache=False)

API SSL feature
---------------

The Cisco Prime Infrastructure API is only accessible trough HTTP over SSL (HTTPS) connections. By default the PIAPI
class verifies the server's SSL certificate. You can disable this behaviour by setting the *verify* argument of the PIAPI
constructor to False.

::

    api = PIAPI("https://pi-server/", "username" , "password", verify=False)

API Timeout handling
--------------------

The Cisco Prime Infrastructure API can be really slow. The default request timeout is set to 300 seconds (5min); this
is usefull for some REST Call for long job reporting. To reduce this timeout simply use the *timeout* parameters of
the request method (seconds as metric).

::

    api = PIAPI("https://pi-server/", "username" , "password", verify=False)
    api.request("MyNotSoLongAction", timeout=20)