import boto3
import os
import json
import re

SSM_DOC_NAME = os.environ.get("SSM_DOC_NAME")
DEPLOY_ENV = os.environ.get("DEPLOY_ENV")
WAIT_FOR_ACTIVITY_ARN = os.environ.get("WAIT_FOR_ASYNC_ACTION_ACTIVITY_ARN")
SSM_PARAM_PREFIX = os.environ.get("SSM_PARAM_PREFIX")
BASTION_SSM_ROLE_ARN = os.environ.get("BASTION_SSM_ROLE_ARN")

ssm_client = boto3.client('ssm')
states_client = boto3.client('stepfunctions')

# pre-compile regex patterns
runfolder_pattern = re.compile('([12][0-9][01][0-9][0123][0-9])_(A01052|A00130)_([0-9]{4})_[A-Z0-9]{10}')

# Instrument ID mapping
instrument_name = {
    "A01052": "Po",
    "A00130": "Baymax"
}


def getSSMParam(name):
    """
    Fetch the parameter with the given name from SSM Parameter Store.
    """
    return ssm_client.get_parameter(
                Name=name,
                WithDecryption=True
           )['Parameter']['Value']


# We could use the in-command notation for Parameter Store parameters as explained here:
# https://docs.aws.amazon.com/systems-manager/latest/userguide/sysman-paramstore-about.html
#   Example of in-command parameter usage:
#   command += f" python {{{{ssm:{SSM_PARAM_PREFIX}samplesheet_check_script_plain}}}} {samplesheet_path} ..."
# However, that does not suppport encrypted parameters!
# Therefore we have to fetch the parameters ourselves
ssm_instance_id = getSSMParam(SSM_PARAM_PREFIX + "ssm_instance_id")
runfolder_base_path = getSSMParam(SSM_PARAM_PREFIX + "runfolder_base_path")
bcl2fastq_base_path = getSSMParam(SSM_PARAM_PREFIX + "bcl2fastq_base_path")
hpc_dest_base_path = getSSMParam(SSM_PARAM_PREFIX + "hpc_dest_base_path")
runfolder_check_script = getSSMParam(SSM_PARAM_PREFIX + "runfolder_check_script")
samplesheet_check_script = getSSMParam(SSM_PARAM_PREFIX + "samplesheet_check_script")
bcl2fastq_script = getSSMParam(SSM_PARAM_PREFIX + "bcl2fastq_script")
checksum_script = getSSMParam(SSM_PARAM_PREFIX + "checksum_script")
hpc_sync_script = getSSMParam(SSM_PARAM_PREFIX + "hpc_sync_script")
s3_sync_script = getSSMParam(SSM_PARAM_PREFIX + "s3_sync_script")
lims_update_script = getSSMParam(SSM_PARAM_PREFIX + "lims_update_script")
multiqc_script = getSSMParam(SSM_PARAM_PREFIX + "multiqc_script")
stats_update_script = getSSMParam(SSM_PARAM_PREFIX + "stats_update_script")
hpc_sync_dest_host = getSSMParam(SSM_PARAM_PREFIX + "hpc_sync_dest_host")
HPC_SSH_USER = getSSMParam(SSM_PARAM_PREFIX + "hpc_sync_ssh_user")
aws_profile = getSSMParam(SSM_PARAM_PREFIX + "aws_profile")
aws_profile_spartan = getSSMParam(SSM_PARAM_PREFIX + "aws_profile_spartan")
s3_sync_dest_bucket = getSSMParam(SSM_PARAM_PREFIX + "s3_sync_dest_bucket")
s3_raw_data_bucket = getSSMParam(SSM_PARAM_PREFIX + "s3_raw_data_bucket")


def build_command(script_case, input_data):
    """
    Builds the remote shell script command to run given the use case, input data and the task token
    to be used to terminate the waiting activity of the Step Function state.
    """

    if input_data.get('runfolder'):
        runfolder = input_data['runfolder']
        print(f"runfolder: {runfolder}")
    else:
        raise ValueError('A runfolder parameter is mandatory!')

    # TODO: PO: extract instrument ID from runfolder and deduct runfolder base path
    try:
        run_date = re.search(runfolder_pattern, runfolder).group(1)
        run_inst_id = re.search(runfolder_pattern, runfolder).group(2)
        run_no = re.search(runfolder_pattern, runfolder).group(3)
    except AttributeError:
        raise ValueError(f"Runfolder name {runfolder} did not match expected format: {runfolder_pattern}")
    print(f"Extracted date/instr_id/run_no from runfolder: {run_date}/{run_inst_id}/{run_no}")

    runfolder_path = os.path.join(runfolder_base_path, instrument_name[run_inst_id], runfolder)
    print(f"Using runfolder path: {runfolder_path}")
    bcl2fastq_out_path = os.path.join(bcl2fastq_base_path, runfolder)
    print(f"Using fastq path: {bcl2fastq_out_path}")

    execution_timneout = '600'  # the (default) time (in sec) before the command is timed out
    command = f"su - limsadmin -c '"

    if script_case == "runfolder_check":
        execution_timneout = '60'
        command += f" DEPLOY_ENV={DEPLOY_ENV}"
        command += f" {runfolder_check_script} {runfolder_path}"
    elif script_case == "samplesheet_check":
        samplesheet_path = os.path.join(runfolder_path, "SampleSheet.csv")
        command += f" conda activate pipeline &&"
        command += f" DEPLOY_ENV={DEPLOY_ENV} AWS_PROFILE={aws_profile}"
        command += f" python {samplesheet_check_script} {samplesheet_path}"
    elif script_case == "bcl2fastq":
        execution_timneout = '72000'
        command += f" DEPLOY_ENV={DEPLOY_ENV} AWS_PROFILE={aws_profile}"
        command += f" {bcl2fastq_script} -R {runfolder_path} -n {runfolder} -o {bcl2fastq_out_path}"
    elif script_case == "create_runfolder_checksums":
        execution_timneout = '36000'
        command += f" DEPLOY_ENV={DEPLOY_ENV} AWS_PROFILE={aws_profile}"
        command += f" {checksum_script} runfolder {runfolder_path} {runfolder}"
    elif script_case == "create_fastq_checksums":
        execution_timneout = '36000'
        command += f" DEPLOY_ENV={DEPLOY_ENV} AWS_PROFILE={aws_profile}"
        command += f" {checksum_script} bcl2fastq {bcl2fastq_out_path} {runfolder}"
    elif script_case == "sync_fastqs_to_s3_spartan":
        execution_timneout = '36000'
        command += f" DEPLOY_ENV={DEPLOY_ENV} AWS_PROFILE={aws_profile_spartan}"
        command += f" {s3_sync_script} -b {s3_sync_dest_bucket} -n {runfolder}"
        command += f" -d {runfolder} -s {bcl2fastq_out_path} -f"
    elif script_case == "sync_runfolder_to_s3":
        execution_timneout = '10800'
        command += f" DEPLOY_ENV={DEPLOY_ENV} AWS_PROFILE={aws_profile}"
        command += f" {s3_sync_script} -b {s3_raw_data_bucket} -n {runfolder}"
        command += f" -d {runfolder} -s {runfolder_path} -x Thumbnail_Images/*"
    elif script_case == "sync_fastqs_to_s3":
        execution_timneout = '36000'
        command += f" DEPLOY_ENV={DEPLOY_ENV} AWS_PROFILE={aws_profile}"
        command += f" {s3_sync_script} -b {s3_sync_dest_bucket} -n {runfolder}"
        command += f" -d {runfolder} -s {bcl2fastq_out_path} -f"
    elif script_case == "google_lims_update":
        command += f" conda activate pipeline &&"
        command += f" DEPLOY_ENV={DEPLOY_ENV}"
        command += f" python {lims_update_script} {runfolder}"
    elif script_case == "create_multiqc_reports":
        command += f" conda activate pipeline &&"
        command += f" DEPLOY_ENV={DEPLOY_ENV} AWS_PROFILE={aws_profile}"
        command += f" {multiqc_script} {runfolder}"
    elif script_case == "stats_sheet_update":
        command += f" conda activate pipeline &&"
        command += f" DEPLOY_ENV={DEPLOY_ENV}"
        command += f" python {stats_update_script} {bcl2fastq_out_path}/Stats_custom.*.truseq/Stats.json"
    else:
        print("Unsupported script_case! Should do something sensible here....")
        raise ValueError("No valid execution script!")
    command += "'"

    print(f"Script command; {command}")

    return command, execution_timneout


def aws_session(role_arn=None, session_name='my_session'):
    """
    If role_arn is given assumes a role and returns boto3 session
    otherwise return a regular session with the current IAM user/role
    """
    if role_arn:
        client = boto3.client('sts')
        response = client.assume_role(RoleArn=role_arn, RoleSessionName=session_name)
        session = boto3.Session(
            aws_access_key_id=response['Credentials']['AccessKeyId'],
            aws_secret_access_key=response['Credentials']['SecretAccessKey'],
            aws_session_token=response['Credentials']['SessionToken'])
        return session
    else:
        return boto3.Session()


def lambda_handler(event, context):
    print(f"Received event: {json.dumps(event)}")

    ############################################################
    # initial checks

    if event.get('script_execution'):
        script_execution = event['script_execution']
        print(f"script_execution: {script_execution}")
    else:
        raise ValueError('A script_execution parameter is mandatory!')

    ############################################################
    # Get the task token from the wait-for activity task

    print("Retrieving task token...")
    print(f"...for activity: {WAIT_FOR_ACTIVITY_ARN} and lambda: {context.function_name}")
    activity_task_response = states_client.get_activity_task(
        activityArn=WAIT_FOR_ACTIVITY_ARN,
        workerName=context.function_name, )

    if 'taskToken' in activity_task_response:
        task_token = activity_task_response["taskToken"]
        print(f"Task token: {task_token[:10]}...{task_token[-10:]}")
    else:
        raise ValueError("Activity response did not yield a task token!")

    ############################################################
    # send a command to the SSM for asynchronous execution

    script_command, script_timeout = build_command(script_case=script_execution,
                                                   input_data=event['input'])

    session_assumed = aws_session(role_arn=BASTION_SSM_ROLE_ARN, session_name='bastion_session')
    response = session_assumed.client('ssm').send_command(
        InstanceIds=[ssm_instance_id],
        DocumentName=SSM_DOC_NAME,
        Parameters={"commands": [script_command], "executionTimeout": [script_timeout],
                    "taskToken": [task_token], "deployEnv": [DEPLOY_ENV]},
        CloudWatchOutputConfig={'CloudWatchOutputEnabled': True}, )

    command_id = response['Command']['CommandId']
    print(f"Command ID: {command_id}")
