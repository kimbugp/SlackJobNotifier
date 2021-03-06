import json
import os
import sys
import time
import uuid

sys.path.append('package')  # noqa
import requests

import boto3
import botocore

URL = "https://boards-api.greenhouse.io/v1/boards/partnerengagementstaffing/jobs"
ACCESS_ID = os.environ.get("ACCESS_KEY_ID")
ACCESS_KEY = os.environ.get("ACCESS_KEY")
SLACK_WEBHOOK = os.environ.get("SLACK_WEBHOOK")
PREFIX = "jobs"


def generate_key_with_date(prefix):
    day = time.strftime("%Y%m%d")
    return "-".join([prefix, day])


def lambda_handler(event, context):
    connection = boto3.resource("s3", aws_access_key_id=ACCESS_ID,
                                aws_secret_access_key=ACCESS_KEY)

    jobs, meta = get_jobs_list()
    bucket, response = create_bucket(PREFIX, connection)

    # create jobs object key by date
    jobs_date_key = generate_key_with_date(PREFIX)
    prev_jobs = previous_data(connection, bucket, jobs_date_key)

    new_jobs = get_new_jobs(prev_jobs, jobs)

    if new_jobs:
        # slack handler
        webhooks = get_webhooks(connection, bucket)
        slack = SlackHelper()
        res = slack.post_message(new_jobs, meta, webhooks)
        copy_to_bucket(bucket, jobs_date_key, jobs, connection)
    print('None found')
    return


def get_new_jobs(prev_jobs, jobs):
    prev_jobs_ids = [job["id"] for job in prev_jobs]
    return [job for job in jobs if job["id"] not in prev_jobs_ids]


def previous_data(connection, bucket, key):
    try:
        bucker_instance = connection.Object(bucket, key)
        obj = bucker_instance.get()["Body"].read()
    except botocore.exceptions.ClientError as e:
        return {}
    return json.loads(obj)


def get_data(bucket_name, key, connection):
    bucker_instance = connection.Object(bucket_name, key)
    serializedObject = bucker_instance.get()["Body"].read()
    return serializedObject.decode('utf-8')


def create_bucket_name(bucket_prefix):
    return "-".join([bucket_prefix, str(uuid.uuid4())])


def check_bucket_name(prefix, buckets):
    for bucket in buckets:
        if bucket.name.startswith(prefix):
            return bucket.name


def create_bucket(bucket_prefix, connection):
    session = boto3.session.Session()
    current_region = session.region_name
    buckets = connection.buckets.all()
    bucket_name = check_bucket_name(bucket_prefix, buckets)
    if bucket_name:
        return bucket_name, False
    # create bucket if none exists with the prefix
    bucket_name = create_bucket_name(bucket_prefix)
    bucket_response = connection.create_bucket(
        Bucket=bucket_name,
        CreateBucketConfiguration={
            "LocationConstraint": current_region})
    return bucket_name, bucket_response


def copy_to_bucket(bucket_name, key, items, connection):
    items = json.dumps(items)
    connection.Bucket(bucket_name).put_object(Key=key, Body=items)


def get_jobs_list():
    response = requests.get(URL)
    json_data = response.json()
    return json_data.get("jobs"), json_data.get("meta")


def get_webhooks(connection, bucket):
    key = 'subscribers'
    data = '[{}]'.format(get_data(bucket, key, connection))
    data = json.loads(data)
    return [hook['url'] for hook in data]


class SlackHelper:

    def post_message(self, jobs, meta, webhooks):
        responses = []
        for web_hook in webhooks:
            res = requests.post(web_hook, json=self.create_slack_message(
                jobs, meta), headers={"Content-Type": "application/json"})
            responses.append(res)
        return responses

    @staticmethod
    def create_slack_message(jobs, meta):
        image_url = 'https://cdn.greenhouse.io/external_greenhouse_job_boards/logos/000/011/710/resized/blue_icon.png'
        job_str = """{{"type": "section","text": {{"type": "mrkdwn","text": "*<{absolute_url}|{title}>*\\nCreated at: {updated_at}\\nLocation:{location[name]}\\nRequistion Id: {requisition_id}\\nInternal Job Id: {internal_job_id}"}},"accessory":{{"type": "image","image_url": "{image_url}","alt_text": "{title}"}}}},{{"type": "divider"}}"""
        jobs_string = ",".join(job_str.format(
            image_url=image_url, **job) for job in jobs)
        message = """{{"blocks": [{{"type": "section","text": {{"type": "mrkdwn","text": "@channel We found {total} new jobs"}}}},{{"type": "divider"}},{job_string}]}}""".format(
            job_string=jobs_string, **meta)
        return json.loads(message)