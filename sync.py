from requests import session
from bs4 import BeautifulSoup
import json
import urllib3
import csv
from pprint import pprint
from canvas_tools import get, post, from_canvas_date, to_canvas_date
from datetime import datetime, timedelta
from dateutil import tz, parser
import os
import sys
import requests

import requests
from requests.packages.urllib3.exceptions import InsecureRequestWarning

requests.packages.urllib3.disable_warnings(InsecureRequestWarning)

# Moodle site does not have HTTPS
urllib3.disable_warnings()

# Load in settings
dir_path = os.path.dirname(os.path.realpath(__file__))
with open(dir_path+'/settings.json') as settings_file:
    settings = json.load(settings_file)

head = { "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/51.0.2704.103 Safari/537.36" }

def log_error(*message):
    print(*message)
def log_info(*message):
    print(*message)

def moodle_get_session_key():
    # Get a session key for this report
    result = s.post(settings['moodle']['urls']['login'], data = {
        'username': settings['moodle']['username'],
        'password': settings['moodle']['password'],
    }, headers=head, verify=False)
    soup = BeautifulSoup(result.content, 'lxml')
    return soup.find('input', {'name': 'sesskey'}).get('value')

VPL_LABEL = 'Virtual programming lab: '
LTI_LABEL = 'External tool: '
def moodle_parse_tool(name):
    if name.startswith(VPL_LABEL):
        name = name[len(VPL_LABEL):]
        tool = VPL_LABEL
    elif name.startswith(LTI_LABEL):
        name = name[len(LTI_LABEL):]
        tool = LTI_LABEL
    else:
        tool = 'Moodle'
    return name, tool

REAL_LABEL = ' (Real)'
def moodle_parse_row(header, row, course=None):
    user_information = dict(zip(header[:7], row[:7]))
    grades = {}
    for name, grade in zip(header[7:-1], row[7:-1]):
        name, tool = moodle_parse_tool(name)
        if name.endswith(REAL_LABEL):
            name = name[:-len(REAL_LABEL)]
        name = name.strip()
        if grade == '-':
            continue
        grades[name] = {
            'grade': float(grade),
            'tool': tool
        }
    return {
        'user': user_information,
        'grades': grades
    }
    
def moodle_get_report(session_key, course_id):
    result = s.post(settings['moodle']['urls']['grade_report_history'], data = {
            'sesskey': session_key,
            'id': course_id,
            'mform_isexpanded_id_gradeitems': 1,
            'checkbox_controller1': 1,
            'mform_isexpanded_id_options': 1,
            '_qf__grade_export_form': 1,
            'export_feedback': 0,
            'export_onlyactive': 0,
            'display[real]': 1,
            'decimals': 2,
            'separator': 'comma',
            'submitbutton': 'Download',
            'showreport': 1,
            'itemid': 0,
            'grader': 0,
            'datefrom': 0,
            'datetill': 0,
            'userids': '',
    }, headers=head, verify=False, stream=True)
    decoded_content = result.content.decode('utf-8')
    cr = csv.reader(decoded_content.splitlines(), delimiter=',')
    header = next(cr)
    for row in cr:
        yield moodle_parse_row(header, row, course_id)

from_zone = tz.tzutc()
to_zone = tz.tzlocal()
now = datetime.now().replace(tzinfo=to_zone)
def within(dt, **distance):
    return (now - dt) <= timedelta(**distance)
        
def canvas_get_assignments(course_id):
    assignments = get('assignments', course_id, all=True,
                      token=settings['canvas']['token'],
                      api_url=settings['canvas']['urls']['api'])
    kept_assignments = []
    for a in assignments:
        if 'lock_at' in a and a['lock_at']:
            if within(to_local_datetime(a['lock_at']), hours=1):
                kept_assignments.append(a)
            else:
                log_info("Assignment past due, skipping:", a['name'])
        elif 'due_at' in a and a['due_at']:
            if within(to_local_datetime(a['due_at']), hours=1):
                kept_assignments.append(a)
            else:
                log_info("Assignment past due, skipping:", a['name'])
        else:
            kept_assignments.append(a)
    assignment_name_map = {
        assignment['name'] : assignment['id']
        for assignment in kept_assignments
    }
    return assignment_name_map
    
def canvas_get_students(course_id):
    students = get('users', course_id, all=True,
                   data={'enrollment_type': 'student'},
                   token=settings['canvas']['token'],
                   api_url=settings['canvas']['urls']['api'])
    student_map = {
        student['email'].lower() : student['id']
        for student in students
    }
    return student_map
    
def canvas_submit_grade(course_id, assignment_id, grade_pairs):
    grades = {}
    for canvas_student_id, grade in grade_pairs.items():
        key = 'grade_data[{}]'.format(canvas_student_id)
        grades[key+'[posted_grade]'] = grade
        #grades[key+'[text_comment]'] = 'Grade automatically retrieved from Quiz'
    response = post('assignments/{}/submissions/update_grades'.format(assignment_id),
                    course_id, data=grades,
                    token=settings['canvas']['token'],
                    api_url=settings['canvas']['urls']['api'])
    
def canvas_collect_grades(grade_report, student_map, assignment_name_map):
    grades = {}
    for student in grade_report:
        email = student['user']['Username'].lower()+"@udel.edu"
        if email not in student_map:
            log_error("Unknown student: {!r}".format(email))
            continue
        canvas_student_id = student_map[email]
        for moodle_assignment, moodle_grade in student['grades'].items():
            if moodle_assignment not in assignment_name_map:
                log_error("Unknown assignment: {!r}".format(moodle_assignment))
                continue
            canvas_assignment_id = assignment_name_map[moodle_assignment]
            if canvas_assignment_id not in grades:
                grades[canvas_assignment_id] = {}
            grade = moodle_grade['grade']
            grades[canvas_assignment_id][canvas_student_id] = grade
    return grades
    
def canvas_filter_unchanged(course_id, grades):
    assignments = {}
    student_ids = []
    for canvas_assignment_id, grade_pairs in grades.items():
        assignments[canvas_assignment_id] = {}
        for canvas_student_id, grade in grade_pairs.items():
            student_ids.append(canvas_student_id)
    submissions = get('students/submissions', course_id, all=True,
                      data={'student_ids[]': student_ids,
                            'assignment_ids[]': list(assignments.keys())},
                      token=settings['canvas']['token'],
                      api_url=settings['canvas']['urls']['api'])
    filtered = {a:[] for a in assignments}
    for submission in submissions:
        assignment_id = submission['assignment_id']
        user_id = submission['user_id']
        old_grade = submission['score']
        if assignment_id not in grades or user_id not in grades[assignment_id]:
            continue
        new_grade = grades[assignment_id][user_id]
        if old_grade == None or new_grade > old_grade:
            assignments[assignment_id][user_id] = new_grade
        else:
            filtered[assignment_id].append(user_id)
    for assignment_id, filtered_users in filtered.items():
        if filtered_users:
            log_info("Filtering out {} for {}".format(assignment_id, ', '.join(map(str, filtered_users))))
    return assignments

def to_local_datetime(canvas_date_string):
    if not canvas_date_string:
        return ''
    return (from_canvas_date(canvas_date_string)
                      .replace(tzinfo=from_zone)
                      .astimezone(to_zone))

FRIENDLY_DATE_FORMAT = "%B %d %Y, %I:%M %p"
log_info("Starting", now.strftime(FRIENDLY_DATE_FORMAT))
with session() as s:
    # Login
    session_key = moodle_get_session_key()
    # Actually grab the report
    for moodle_id, canvas_id in settings['conversion']['courses'].items():
        log_info("Looking at Moodle course", moodle_id)
        if canvas_id is False:
            continue
        log_info("Getting grade report")
        grade_report = moodle_get_report(session_key, moodle_id)
        log_info("Getting assignments from Canvas")
        assignment_name_map = canvas_get_assignments(canvas_id)
        assignment_name_map_r = {v:k for k,v in assignment_name_map.items()}
        log_info("Getting students from Canvas")
        student_map = canvas_get_students(canvas_id)
        log_info("Organizing grades")
        grades = canvas_collect_grades(grade_report, student_map, assignment_name_map)
        grades = canvas_filter_unchanged(canvas_id, grades)
        for canvas_assignment_id, grade_pairs in grades.items():
            if not grade_pairs:
                continue
            assignment_name = assignment_name_map_r[canvas_assignment_id]
            log_info("Submitting {} grades for assignment {!r} ({})".format(len(grade_pairs), assignment_name, canvas_assignment_id))
            canvas_submit_grade(canvas_id, canvas_assignment_id, grade_pairs)
log_info("Script completed")
log_info()
