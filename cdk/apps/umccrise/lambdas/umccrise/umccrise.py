import os
import json
import boto3

batch_client = boto3.client('batch')
s3 = boto3.client('s3')


def job_name_form_s3(bucket, path):
    # Construct a meaningful default job name from bucket and S3 prefix
    # taking care of special characters that would otherwise cause issues downstream
    return bucket + "---" + path.replace('/', '_').replace('.', '_')


def lambda_handler(event, context):
    # Log the received event
    print("Received event: " + json.dumps(event, indent=2))
    # Get parameters for the SubmitJob call
    # http://docs.aws.amazon.com/batch/latest/APIReference/API_SubmitJob.html

    # overwrite parameters if defined in the event/request, else use defaults from the environment
    # containerOverrides, dependsOn, and parameters are optional
    container_overrides = event['containerOverrides'] if event.get('containerOverrides') else {}
    parameters = event['parameters'] if event.get('parameters') else {}
    depends_on = event['dependsOn'] if event.get('dependsOn') else []
    job_queue = event['jobQueue'] if event.get('jobQueue') else os.environ.get('JOBQUEUE')
    job_definition = event['jobDefinition'] if event.get('jobDefinition') else os.environ.get('JOBDEF')

    container_mem = event['memory'] if event.get('memory') else os.environ.get('UMCCRISE_MEM')
    container_vcpus = event['vcpus'] if event.get('vcpus') else os.environ.get('UMCCRISE_VCPUS')
    data_bucket = event['dataBucket'] if event.get('dataBucket') else os.environ.get('DATA_BUCKET')
    result_bucket = event['resultBucket'] if event.get('resultBucket') else data_bucket
    refdata_bucket = event['refDataBucket'] if event.get('refDataBucket') else os.environ.get('REFDATA_BUCKET')
    result_dir = event['resultDir']
    job_name = event['jobName'] if event.get('jobName') else job_name_form_s3(data_bucket, result_dir)
    job_name = os.environ.get('JOBNAME_PREFIX') + '_' + job_name
    print("resultDir: %s  in data bucket: %s" % (result_dir, data_bucket))

    try:
        response = s3.list_objects(Bucket=data_bucket, MaxKeys=5, Prefix=result_dir)
        print("S3 list response: " + json.dumps(response, indent=2, sort_keys=True, default=str))
        if not response.get('Contents') or len(response['Contents']) < 1:
            return {
                'statusCode': 400,
                'error': 'Bad parameter',
                'message': f"Provided S3 path ({result_dir}) does not exist in bucket {data_bucket}!"
            }

        # Inject S3 object from the data_bucket into parameters for AWS Batch and
        # inside the docker container
        # container_overrides = {'environment': [{'name': 'S3_INPUT_DIR', 'value': key}]}
        container_overrides['environment'] = [
            {'name': 'S3_INPUT_DIR', 'value': result_dir},
            {'name': 'S3_DATA_BUCKET', 'value': data_bucket},
            {'name': 'S3_RESULT_BUCKET', 'value': result_bucket},
            {'name': 'S3_REFDATA_BUCKET', 'value': refdata_bucket},
            {'name': 'CONTAINER_VCPUS', 'value': container_vcpus},
            {'name': 'CONTAINER_MEM', 'value': container_mem}
        ]
        if container_mem:
            container_overrides['memory'] = int(container_mem)
        if container_vcpus:
            container_overrides['vcpus'] = int(container_vcpus)
            parameters['vcpus'] = container_vcpus

        print("jobName: " + job_name)
        print("jobQueue: " + job_queue)
        print("parameters: ")
        print(parameters)
        print("dependsOn: ")
        print(depends_on)
        print("containerOverrides: ")
        print(container_overrides)
        print("jobDefinition: ")
        print(job_definition)
        response = batch_client.submit_job(
            dependsOn=depends_on,
            containerOverrides=container_overrides,
            jobDefinition=job_definition,
            jobName=job_name,
            jobQueue=job_queue,
            parameters=parameters
        )

        # Log response from AWS Batch
        print("Batch submit job response: " + json.dumps(response, indent=2))
        # Return the jobId
        event['jobId'] = response['jobId']
        return event
    except Exception as e:
        print(e)