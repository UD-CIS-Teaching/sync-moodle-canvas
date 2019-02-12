import json
import requests
import os, sys
import time
from datetime import datetime

def _canvas_request(verb, command, course_id, data, all, params, json, token, api_url):
    try:
        if data is None:
            data = {}
        if params is None:
            params = {}
        headers = {}
        if json is not None:
            data = None
            headers['Authorization'] = "Bearer "+token
        else:
            data['access_token'] = token
        next_url = api_url
        next_url += 'courses/{course_id}/'.format(course_id=course_id)
        next_url += command
        if all:
            data['per_page'] = 100
            final_result = []
            while True:
                response = verb(next_url, data=data, params=params, json=json, headers=headers)
                final_result += response.json()
                if 'next' in response.links:
                    next_url = response.links['next']['url']
                else:
                    return final_result
        else:
            response = verb(next_url, data=data, params=params, json=json, headers=headers)
            if response.status_code == 204:
                return response
            return response.json()
    except json.decoder.JSONDecodeError:
        raise Exception("{}\n{}".format(response, next_url))
    
def get(command, course='default', data=None, all=False, params=None, json=None, token=None, api_url=None):
    return _canvas_request(requests.get, command, course, data, all, params, json, token, api_url)
    
def post(command, course='default', data=None, all=False, params=None, json=None, token=None, api_url=None):
    return _canvas_request(requests.post, command, course, data, all, params, json, token, api_url)
    
def put(command, course='default', data=None, all=False, params=None, json=None, token=None, api_url=None):
    return _canvas_request(requests.put, command, course, data, all, params, json, token, api_url)
    
def delete(command, course='default', data=None, all=False, params=None, json=None, token=None, api_url=None):
    return _canvas_request(requests.delete, command, course, data, all, params, json, token, api_url)

def progress_loop(progress_id, DELAY=3):
    attempt = 0
    while True:
        result = _canvas_request(requests.get, 'progress/{}'.format(progress_id), 
                                 None, {'_dummy_counter': attempt}, 
                                 False, None, dict)[0]
        if result['workflow_state'] == 'completed':
            return True
        elif result['workflow_state'] == 'failed':
            return False
        else:
            print("In progress:", result['workflow_state'], result['message'], 
                  str(int(round(result['completion']*10))/10)+"%")
            if not hasattr(result, 'from_cache') or not result.from_cache:
                time.sleep(DELAY)
            attempt += 1
            
def download_file(url, destination):
    data = {'access_token': get_setting('canvas-token')}
    r = requests.get(url)
    f = open(destination, 'wb')
    for chunk in r.iter_content(chunk_size=512 * 1024): 
        if chunk: # filter out keep-alive new chunks
            f.write(chunk)
    f.close()

CANVAS_DATE_STRING = "%Y-%m-%dT%H:%M:%SZ"

def from_canvas_date(d1):
    return datetime.strptime(d1, CANVAS_DATE_STRING)

def to_canvas_date(d1):
    return d1.strftime(CANVAS_DATE_STRING)
