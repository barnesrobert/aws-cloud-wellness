
"""Summary

Attributes:
    AWS_CLOUD_WELLNESS_STANDARD_VERSION (str): Description
    CONFIG_RULE (bool): Description
    CONTROL_1_1_DAYS (int): Description
    IAM_CLIENT (TYPE): Description
    REGIONS (list): Description
    S3_WEB_REPORT (bool): Description
    S3_WEB_REPORT_BUCKET (str): Description
    S3_WEB_REPORT_EXPIRE (str): Description
    S3_WEB_REPORT_OBFUSCATE_ACCOUNT (bool): Description
    SCRIPT_OUTPUT_JSON (bool): Description
"""

from __future__ import print_function
import json
import csv
import time
import sys
import re
import tempfile
import getopt
import os
from datetime import datetime
import boto3


''' TODO:
    Add suppression argument.
    Add additional canned Config Rules (S3)
    Add monitoring control for GuardDuty alerts
'''


# --- Script controls ---

# AWS Cloud Wellness version referenced. Only used in web report.
AWS_CLOUD_WELLNESS_STANDARD_VERSION = "1.0"

# Would you like a HTML file generated with the result?
# This file will be delivered using a signed URL.
S3_WEB_REPORT = True

# Where should the report be delivered to?
# Make sure to update permissions for the Lambda role if you change bucket name.
# Alternatively, pass the bucket name in the --output-bucket parameter.
S3_WEB_REPORT_BUCKET = ""

# Create separate report files?
# This will add date and account number as prefix. Example: aws_cloud_wellness_report_111111111111_161220_1213.html
S3_WEB_REPORT_NAME_DETAILS = True

# How many hours should the report be available? Default = 168h/7days
S3_WEB_REPORT_EXPIRE = "168"

# Set to true if you wish to anonymize the account number in the report.
# This is mostly used for demo/sharing purposes.
S3_WEB_REPORT_OBFUSCATE_ACCOUNT = False

# Would  you like to send the report signedURL to an SNS topic
SEND_REPORT_URL_TO_SNS = False
SNS_TOPIC_ARN = "CHANGE_ME_TO_YOUR_TOPIC_ARN"

# Would you like to print the results as JSON to output?
SCRIPT_OUTPUT_JSON = True

# Would you like to supress all output except JSON result?
# Can be used when you want to pipe result to another system.
# If using S3 reporting, please enable SNS integration to get S3 signed URL
OUTPUT_ONLY_JSON = False


# --- Control Parameters ---

# Control 1.18 - IAM manager and master role names <Not implemented yet, under review>
IAM_MASTER = "iam_master"
IAM_MANAGER = "iam_manager"
IAM_MASTER_POLICY = "iam_master_policy"
IAM_MANAGER_POLICY = "iam_manager_policy"

# Control 1.1 - Days allowed since use of root account.
CONTROL_1_1_DAYS = 0


# --- Global ---
IAM_CLIENT = boto3.client('iam')
S3_CLIENT = boto3.client('s3')

CONTROL_LABEL_MAP = {"1": "IAM", "2": "Logging",
                     "3": "Monitoring", "4": "Networking", "5": "Custom"}

output_bucket = ''


# --- 1 Identity and Access Management ---

# 1.1 Avoid the use of the "root" account (Scored)
def control_1_1_root_use(credreport):
    """Summary

    Args:
        credreport (TYPE): Description

    Returns:
        TYPE: Description
    """
    result = True
    failReason = ""
    offenders = []
    offenders_links = []
    control = "1.1"
    description = "Avoid the use of the root account"
    scored = True
    if "Fail" in credreport:  # Report failure in control
        sys.exit(credreport)
    # Check if root is used in the last 24h
    now = time.strftime('%Y-%m-%dT%H:%M:%S+00:00', time.gmtime(time.time()))
    frm = "%Y-%m-%dT%H:%M:%S+00:00"

    try:
        pwdDelta = (datetime.strptime(now, frm) -
                    datetime.strptime(credreport[0]['password_last_used'], frm))
        if (pwdDelta.days == CONTROL_1_1_DAYS) & (pwdDelta.seconds > 0):  # Used within last 24h
            failReason = "Used within 24h"
            result = False
    except:
        if credreport[0]['password_last_used'] == "N/A" or "no_information":
            pass
        else:
            print("Something went wrong")

    try:
        key1Delta = (datetime.strptime(now, frm) - datetime.strptime(credreport[0]['access_key_1_last_used_date'], frm))
        if (key1Delta.days == CONTROL_1_1_DAYS) & (key1Delta.seconds > 0):  # Used within last 24h
            failReason = "Used within 24h"
            result = False
    except:
        if credreport[0]['access_key_1_last_used_date'] == "N/A" or "no_information":
            pass
        else:
            print("Something went wrong")
    try:
        key2Delta = datetime.strptime(now, frm) - datetime.strptime(credreport[0]['access_key_2_last_used_date'], frm)
        if (key2Delta.days == CONTROL_1_1_DAYS) & (key2Delta.seconds > 0):  # Used within last 24h
            failReason = "Used within 24h"
            result = False
    except:
        if credreport[0]['access_key_2_last_used_date'] == "N/A" or "no_information":
            pass
        else:
            print("Something went wrong")
    return {'Result': result, 'failReason': failReason, 'Offenders': offenders, 'OffendersLinks': offenders_links, 'ScoredControl': scored, 'Description': description, 'ControlId': control}


# 1.2 Ensure multi-factor authentication (MFA) is enabled for all IAM users that have a console password (Scored)
def control_1_2_mfa_on_password_enabled_iam(credreport):
    """Summary

    Args:
        credreport (TYPE): Description

    Returns:
        TYPE: Description
    """
    result = True
    failReason = ""
    offenders = []
    offenders_links = []
    control = "1.2"
    description = "Ensure multi-factor authentication (MFA) is enabled for all IAM users that have a console password"
    scored = True
    for i in range(len(credreport)):
        # Verify if the user have a password configured
        if credreport[i]['password_enabled'] == "true":
            # Verify if password users have MFA assigned
            if credreport[i]['mfa_active'] == "false":
                result = False
                failReason = "No MFA on users with password. "
                offenders.append(str(credreport[i]['arn']))
                offenders_links.append('https://console.aws.amazon.com/iam/home#/users/{user}?section=security_credentials'.format(user=credreport[i]['user']))

    return {'Result': result, 'failReason': failReason, 'Offenders': offenders, 'OffendersLinks': offenders_links, 'ScoredControl': scored, 'Description': description, 'ControlId': control}


# 1.3 Ensure credentials unused for 90 days or greater are disabled (Scored)
def control_1_3_unused_credentials(credreport):
    """Summary

    Args:
        credreport (TYPE): Description

    Returns:
        TYPE: Description
    """
    result = True
    failReason = ""
    offenders = []
    offenders_links = []
    control = "1.3"
    description = "Ensure credentials unused for 90 days or greater are disabled"
    scored = True
    # Get current time
    now = time.strftime('%Y-%m-%dT%H:%M:%S+00:00', time.gmtime(time.time()))
    frm = "%Y-%m-%dT%H:%M:%S+00:00"

    # Look for unused credentails
    for i in range(len(credreport)):
        if credreport[i]['password_enabled'] == "true":
            try:
                delta = datetime.strptime(now, frm) - datetime.strptime(credreport[i]['password_last_used'], frm)
                # Verify password have been used in the last 90 days
                if delta.days > 90:
                    result = False
                    failReason = "Credentials unused > 90 days detected. "
                    offenders.append(str(credreport[i]['arn']) + ":password")
                    offenders_links.append('https://console.aws.amazon.com/iam/home#/users/{user}?section=security_credentials'.format(user=credreport[i]['user']))
            except:
                pass  # Never used
        if credreport[i]['access_key_1_active'] == "true":
            try:
                delta = datetime.strptime(now, frm) - datetime.strptime(credreport[i]['access_key_1_last_used_date'], frm)
                # Verify password have been used in the last 90 days
                if delta.days > 90:
                    result = False
                    failReason = "Credentials unused > 90 days detected. "
                    offenders.append(str(credreport[i]['arn']) + ":key1")
                    offenders_links.append('https://console.aws.amazon.com/iam/home#/users/{user}?section=security_credentials'.format(user=credreport[i]['user']))
            except:
                pass
        if credreport[i]['access_key_2_active'] == "true":
            try:
                delta = datetime.strptime(now, frm) - datetime.strptime(credreport[i]['access_key_2_last_used_date'], frm)
                # Verify password have been used in the last 90 days
                if delta.days > 90:
                    result = False
                    failReason = "Credentials unused > 90 days detected. "
                    offenders.append(str(credreport[i]['arn']) + ":key2")
                    offenders_links.append('https://console.aws.amazon.com/iam/home#/users/{user}?section=security_credentials'.format(user=credreport[i]['user']))
            except:
                # Never used
                pass
    return {'Result': result, 'failReason': failReason, 'Offenders': offenders, 'OffendersLinks': offenders_links, 'ScoredControl': scored, 'Description': description, 'ControlId': control}


# 1.4 Ensure access keys are rotated every 90 days or less (Scored)
def control_1_4_rotated_keys(credreport):
    """Summary

    Args:
        credreport (TYPE): Description

    Returns:
        TYPE: Description
    """
    result = True
    failReason = ""
    offenders = []
    offenders_links = []
    control = "1.4"
    description = "Ensure access keys are rotated every 90 days or less"
    scored = True
    # Get current time
    now = time.strftime('%Y-%m-%dT%H:%M:%S+00:00', time.gmtime(time.time()))
    frm = "%Y-%m-%dT%H:%M:%S+00:00"

    # Look for unused credentails
    for i in range(len(credreport)):
        if credreport[i]['access_key_1_active'] == "true":
            try:
                delta = datetime.strptime(now, frm) - datetime.strptime(credreport[i]['access_key_1_last_rotated'], frm)
                # Verify keys have rotated in the last 90 days
                if delta.days > 90:
                    result = False
                    failReason = "Key rotation >90 days or not used since rotation"
                    offenders.append(str(credreport[i]['arn']) + ":unrotated key1")
                    offenders_links.append('https://console.aws.amazon.com/iam/home#/users/{user}?section=security_credentials'.format(user=credreport[i]['user']))
            except:
                pass
            try:
                last_used_datetime = datetime.strptime(credreport[i]['access_key_1_last_used_date'], frm)
                last_rotated_datetime = datetime.strptime(credreport[i]['access_key_1_last_rotated'], frm)
                # Verify keys have been used since rotation.
                if last_used_datetime < last_rotated_datetime:
                    result = False
                    failReason = "Key rotation >90 days or not used since rotation"
                    offenders.append(str(credreport[i]['arn']) + ":unused key1")
                    offenders_links.append('https://console.aws.amazon.com/iam/home#/users/{user}?section=security_credentials'.format(user=credreport[i]['user']))
            except:
                pass
        if credreport[i]['access_key_2_active'] == "true":
            try:
                delta = datetime.strptime(now, frm) - datetime.strptime(credreport[i]['access_key_2_last_rotated'], frm)
                # Verify keys have rotated in the last 90 days
                if delta.days > 90:
                    result = False
                    failReason = "Key rotation >90 days or not used since rotation"
                    offenders.append(str(credreport[i]['arn']) + ":unrotated key2")
                    offenders_links.append('https://console.aws.amazon.com/iam/home#/users/{user}?section=security_credentials'.format(user=credreport[i]['user']))
            except:
                pass
            try:
                last_used_datetime = datetime.strptime(credreport[i]['access_key_2_last_used_date'], frm)
                last_rotated_datetime = datetime.strptime(credreport[i]['access_key_2_last_rotated'], frm)
                # Verify keys have been used since rotation.
                if last_used_datetime < last_rotated_datetime:
                    result = False
                    failReason = "Key rotation >90 days or not used since rotation"
                    offenders.append(str(credreport[i]['arn']) + ":unused key2")
                    offenders_links.append('https://console.aws.amazon.com/iam/home#/users/{user}?section=security_credentials'.format(user=credreport[i]['user']))
            except:
                pass
    return {'Result': result, 'failReason': failReason, 'Offenders': offenders, 'OffendersLinks': offenders_links, 'ScoredControl': scored, 'Description': description, 'ControlId': control}


# 1.5 Ensure IAM password policy requires at least one uppercase letter (Scored)
def control_1_5_password_policy_uppercase(passwordpolicy):
    """Summary

    Args:
        passwordpolicy (TYPE): Description

    Returns:
        TYPE: Description
    """
    result = True
    failReason = ""
    offenders = []
    offenders_links = []
    control = "1.5"
    description = "Ensure IAM password policy requires at least one uppercase letter"
    scored = True
    if passwordpolicy is False:
        result = False
        failReason = "Account does not have an IAM password policy."
        offenders.append('Account')
        offenders_links.append('https://console.aws.amazon.com/iam/home?#/account_settings')
    else:
        if passwordpolicy['RequireUppercaseCharacters'] is False:
            result = False
            failReason = "Password policy does not require at least one uppercase letter"
            offenders.append('Account')
            offenders_links.append('https://console.aws.amazon.com/iam/home?#/account_settings')
    return {'Result': result, 'failReason': failReason, 'Offenders': offenders, 'OffendersLinks': offenders_links, 'ScoredControl': scored, 'Description': description, 'ControlId': control}


# 1.6 Ensure IAM password policy requires at least one lowercase letter (Scored)
def control_1_6_password_policy_lowercase(passwordpolicy):
    """Summary

    Args:
        passwordpolicy (TYPE): Description

    Returns:
        TYPE: Description
    """
    result = True
    failReason = ""
    offenders = []
    offenders_links = []
    control = "1.6"
    description = "Ensure IAM password policy requires at least one lowercase letter"
    scored = True
    if passwordpolicy is False:
        result = False
        failReason = "Account does not have an IAM password policy."
        offenders.append('Account')
        offenders_links.append('https://console.aws.amazon.com/iam/home?#/account_settings')
    else:
        if passwordpolicy['RequireLowercaseCharacters'] is False:
            result = False
            failReason = "Password policy does not require at least one uppercase letter"
        offenders.append('Account')
        offenders_links.append('https://console.aws.amazon.com/iam/home?#/account_settings')
    return {'Result': result, 'failReason': failReason, 'Offenders': offenders, 'OffendersLinks': offenders_links, 'ScoredControl': scored, 'Description': description, 'ControlId': control}


# 1.7 Ensure IAM password policy requires at least one symbol (Scored)
def control_1_7_password_policy_symbol(passwordpolicy):
    """Summary

    Args:
        passwordpolicy (TYPE): Description

    Returns:
        TYPE: Description
    """
    result = True
    failReason = ""
    offenders = []
    offenders_links = []
    control = "1.7"
    description = "Ensure IAM password policy requires at least one symbol"
    scored = True
    if passwordpolicy is False:
        result = False
        failReason = "Account does not have an IAM password policy."
        offenders.append('Account')
        offenders_links.append('https://console.aws.amazon.com/iam/home?#/account_settings')
    else:
        if passwordpolicy['RequireSymbols'] is False:
            result = False
            failReason = "Password policy does not require at least one symbol"
            offenders.append('Account')
            offenders_links.append('https://console.aws.amazon.com/iam/home?#/account_settings')
    return {'Result': result, 'failReason': failReason, 'Offenders': offenders, 'OffendersLinks': offenders_links, 'ScoredControl': scored, 'Description': description, 'ControlId': control}


# 1.8 Ensure IAM password policy requires at least one number (Scored)
def control_1_8_password_policy_number(passwordpolicy):
    """Summary

    Args:
        passwordpolicy (TYPE): Description

    Returns:
        TYPE: Description
    """
    result = True
    failReason = ""
    offenders = []
    offenders_links = []
    control = "1.8"
    description = "Ensure IAM password policy requires at least one number"
    scored = True
    if passwordpolicy is False:
        result = False
        failReason = "Account does not have an IAM password policy."
        offenders.append('Account')
        offenders_links.append('https://console.aws.amazon.com/iam/home?#/account_settings')
    else:
        if passwordpolicy['RequireNumbers'] is False:
            result = False
            failReason = "Password policy does not require at least one number"
            offenders.append('Account')
            offenders_links.append('https://console.aws.amazon.com/iam/home?#/account_settings')
    return {'Result': result, 'failReason': failReason, 'Offenders': offenders, 'OffendersLinks': offenders_links, 'ScoredControl': scored, 'Description': description, 'ControlId': control}


# 1.9 Ensure IAM password policy requires minimum length of 14 or greater (Scored)
def control_1_9_password_policy_length(passwordpolicy):
    """Summary

    Args:
        passwordpolicy (TYPE): Description

    Returns:
        TYPE: Description
    """
    result = True
    failReason = ""
    offenders = []
    offenders_links = []
    control = "1.9"
    description = "Ensure IAM password policy requires minimum length of 14 or greater"
    scored = True
    if passwordpolicy is False:
        result = False
        failReason = "Account does not have an IAM password policy."
        offenders.append('Account')
        offenders_links.append('https://console.aws.amazon.com/iam/home?#/account_settings')
    else:
        if passwordpolicy['MinimumPasswordLength'] < 14:
            result = False
            failReason = "Password policy does not require at least 14 characters"
            offenders.append('Account')
            offenders_links.append('https://console.aws.amazon.com/iam/home?#/account_settings')
    return {'Result': result, 'failReason': failReason, 'Offenders': offenders, 'OffendersLinks': offenders_links, 'ScoredControl': scored, 'Description': description, 'ControlId': control}


# 1.10 Ensure IAM password policy prevents password reuse (Scored)
def control_1_10_password_policy_reuse(passwordpolicy):
    """Summary

    Args:
        passwordpolicy (TYPE): Description

    Returns:
        TYPE: Description
    """
    result = True
    failReason = ""
    offenders = []
    offenders_links = []
    control = "1.10"
    description = "Ensure IAM password policy prevents password reuse"
    scored = True
    if passwordpolicy is False:
        result = False
        failReason = "Account does not have an IAM password policy."
        offenders.append('Account')
        offenders_links.append('https://console.aws.amazon.com/iam/home?#/account_settings')
    else:
        try:
            if passwordpolicy['PasswordReusePrevention'] == 24:
                pass
            else:
                result = False
                failReason = "Password policy does not prevent reusing last 24 passwords"
                offenders.append('Account')
                offenders_links.append('https://console.aws.amazon.com/iam/home?#/account_settings')
        except:
            result = False
            failReason = "Password policy does not prevent reusing last 24 passwords"
            offenders.append('Account')
            offenders_links.append('https://console.aws.amazon.com/iam/home?#/account_settings')
    return {'Result': result, 'failReason': failReason, 'Offenders': offenders, 'OffendersLinks': offenders_links, 'ScoredControl': scored, 'Description': description, 'ControlId': control}


# 1.11 Ensure IAM password policy expires passwords within 90 days or less (Scored)
def control_1_11_password_policy_expire(passwordpolicy):
    """Summary

    Args:
        passwordpolicy (TYPE): Description

    Returns:
        TYPE: Description
    """
    result = True
    failReason = ""
    offenders = []
    offenders_links = []
    control = "1.11"
    description = "Ensure IAM password policy expires passwords within 90 days or less"
    scored = True
    if passwordpolicy is False:
        result = False
        failReason = "Account does not have an IAM password policy."
        offenders.append('Account')
        offenders_links.append('https://console.aws.amazon.com/iam/home?#/account_settings')
    else:
        if passwordpolicy['ExpirePasswords'] is True:
            if 0 < passwordpolicy['MaxPasswordAge'] > 90:
                result = False
                failReason = "Password policy does not expire passwords after 90 days or less"
                offenders.append('Account')
                offenders_links.append('https://console.aws.amazon.com/iam/home?#/account_settings')
        else:
            result = False
            failReason = "Password policy does not expire passwords after 90 days or less"
            offenders.append('Account')
            offenders_links.append('https://console.aws.amazon.com/iam/home?#/account_settings')
    return {'Result': result, 'failReason': failReason, 'Offenders': offenders, 'OffendersLinks': offenders_links, 'ScoredControl': scored, 'Description': description, 'ControlId': control}


# 1.12 Ensure no root account access key exists (Scored)
def control_1_12_root_key_exists(credreport):
    """Summary

    Args:
        credreport (TYPE): Description

    Returns:
        TYPE: Description
    """
    result = True
    failReason = ""
    offenders = []
    offenders_links = []
    control = "1.12"
    description = "Ensure no root account access key exists"
    scored = True
    if (credreport[0]['access_key_1_active'] == "true") or (credreport[0]['access_key_2_active'] == "true"):
        result = False
        failReason = "Root have active access keys"
    return {'Result': result, 'failReason': failReason, 'Offenders': offenders, 'OffendersLinks': offenders_links, 'ScoredControl': scored, 'Description': description, 'ControlId': control}


# 1.13 Ensure MFA is enabled for the "root" account (Scored)
def control_1_13_root_mfa_enabled():
    """Summary

    Returns:
        TYPE: Description
    """
    result = True
    failReason = ""
    offenders = []
    offenders_links = []
    control = "1.13"
    description = "Ensure MFA is enabled for the root account"
    scored = True
    response = IAM_CLIENT.get_account_summary()
    if response['SummaryMap']['AccountMFAEnabled'] != 1:
        result = False
        failReason = "Root account not using MFA"
    return {'Result': result, 'failReason': failReason, 'Offenders': offenders, 'OffendersLinks': offenders_links, 'ScoredControl': scored, 'Description': description, 'ControlId': control}


# 1.14 Ensure hardware MFA is enabled for the "root" account (Scored)
def control_1_14_root_hardware_mfa_enabled():
    """Summary

    Returns:
        TYPE: Description
    """
    result = True
    failReason = ""
    offenders = []
    offenders_links = []
    control = "1.14"
    description = "Ensure hardware MFA is enabled for the root account"
    scored = True
    # First verify that root is using MFA (avoiding false positive)
    response = IAM_CLIENT.get_account_summary()
    if response['SummaryMap']['AccountMFAEnabled'] == 1:
        paginator = IAM_CLIENT.get_paginator('list_virtual_mfa_devices')
        response_iterator = paginator.paginate(AssignmentStatus='Any',
        )
        pagedResult = []
        for page in response_iterator:
            for n in page['VirtualMFADevices']:
                pagedResult.append(n)
        if "mfa/root-account-mfa-device" in str(pagedResult):
            failReason = "Root account not using hardware MFA"
            result = False
    else:
        result = False
        failReason = "Root account not using MFA"
    return {'Result': result, 'failReason': failReason, 'Offenders': offenders, 'OffendersLinks': offenders_links, 'ScoredControl': scored, 'Description': description, 'ControlId': control}


# 1.15 Ensure security questions are registered in the AWS account (Not Scored/Manual)
def control_1_15_security_questions_registered():
    """Summary

    Returns:
        TYPE: Description
    """
    result = "Manual"
    failReason = ""
    offenders = []
    offenders_links = []
    control = "1.15"
    description = "Ensure security questions are registered in the AWS account, please verify manually"
    scored = False
    failReason = "Control not implemented using API, please verify manually"
    return {'Result': result, 'failReason': failReason, 'Offenders': offenders, 'OffendersLinks': offenders_links, 'ScoredControl': scored, 'Description': description, 'ControlId': control}


# 1.16 Ensure IAM policies are attached only to groups or roles (Scored)
def control_1_16_no_policies_on_iam_users():
    """Summary

    Returns:
        TYPE: Description
    """
    result = True
    failReason = ""
    offenders = []
    offenders_links = []
    control = "1.16"
    description = "Ensure IAM policies are attached only to groups or roles"
    scored = True
    paginator = IAM_CLIENT.get_paginator('list_users')
    response_iterator = paginator.paginate()
    pagedResult = []
    for page in response_iterator:
        for n in page['Users']:
            pagedResult.append(n)
    offenders = []
    offenders_links = []
    for n in pagedResult:
        policies = IAM_CLIENT.list_user_policies(UserName=n['UserName'],
            MaxItems=1
        )
        if policies['PolicyNames'] != []:
            result = False
            failReason = "IAM user have inline policy attached"
            offenders.append(str(n['Arn']))
            offenders_links.append('https://console.aws.amazon.com/iam/home#/users/{user}?section=permissions'.format(user=n['UserName']))
    return {'Result': result, 'failReason': failReason, 'Offenders': offenders, 'OffendersLinks': offenders_links, 'ScoredControl': scored, 'Description': description, 'ControlId': control}


# 1.17 Enable detailed billing (Scored)
def control_1_17_detailed_billing_enabled():
    """Summary

    Returns:
        TYPE: Description
    """
    result = "Manual"
    failReason = ""
    offenders = []
    offenders_links = []
    control = "1.17"
    description = "Enable detailed billing, please verify manually"
    scored = True
    failReason = "Control not implemented using API, please verify manually"
    return {'Result': result, 'failReason': failReason, 'Offenders': offenders, 'OffendersLinks': offenders_links, 'ScoredControl': scored, 'Description': description, 'ControlId': control}


# 1.18 Ensure IAM Master and IAM Manager roles are active (Scored)
def control_1_18_ensure_iam_master_and_manager_roles():
    """Summary

    Returns:
        TYPE: Description
    """
    result = "True"
    failReason = "No IAM Master or IAM Manager role created"
    offenders = []
    offenders_links = []
    control = "1.18"
    description = "Ensure IAM Master and IAM Manager roles are active. Control under review/investigation"
    scored = True
    return {'Result': result, 'failReason': failReason, 'Offenders': offenders, 'OffendersLinks': offenders_links, 'ScoredControl': scored, 'Description': description, 'ControlId': control}


# 1.19 Maintain current contact details (Scored)
def control_1_19_maintain_current_contact_details():
    """Summary

    Returns:
        TYPE: Description
    """
    result = "Manual"
    failReason = ""
    offenders = []
    offenders_links = []
    control = "1.19"
    description = "Maintain current contact details, please verify manually"
    scored = True
    failReason = "Control not implemented using API, please verify manually"
    return {'Result': result, 'failReason': failReason, 'Offenders': offenders, 'OffendersLinks': offenders_links, 'ScoredControl': scored, 'Description': description, 'ControlId': control}


# 1.20 Ensure security contact information is registered (Scored)
def control_1_20_ensure_security_contact_details():
    """Summary

    Returns:
        TYPE: Description
    """
    result = "Manual"
    failReason = ""
    offenders = []
    offenders_links = []
    control = "1.20"
    description = "Ensure security contact information is registered, please verify manually"
    scored = True
    failReason = "Control not implemented using API, please verify manually"
    return {'Result': result, 'failReason': failReason, 'Offenders': offenders, 'OffendersLinks': offenders_links, 'ScoredControl': scored, 'Description': description, 'ControlId': control}


# 1.21 Ensure IAM instance roles are used for AWS resource access from instances (Scored)
def control_1_21_ensure_iam_instance_roles_used():
    """Summary

    Returns:
        TYPE: Description
    """
    result = True
    failReason = ""
    offenders = []
    offenders_links = []
    control = "1.21"
    description = "Ensure IAM instance roles are used for AWS resource access from instances, application code is not audited"
    scored = True
    failReason = "Instance not assigned IAM role for EC2"
    client = boto3.client('ec2')
    response = client.describe_instances()
    offenders = []
    offenders_links = []
    for n, _ in enumerate(response['Reservations']):
        try:
            if response['Reservations'][n]['Instances'][0]['IamInstanceProfile']:
                pass
        except:
            result = False
            offenders.append(str(response['Reservations'][n]['Instances'][0]['InstanceId']))
            offenders_links.append('https://console.aws.amazon.com/ec2/v2/home#Instances:search={instance}'.format(
                instance=response['Reservations'][n]['Instances'][0]['InstanceId']))
    return {'Result': result, 'failReason': failReason, 'Offenders': offenders, 'OffendersLinks': offenders_links, 'ScoredControl': scored, 'Description': description, 'ControlId': control}


# 1.22 Ensure a support role has been created to manage incidents with AWS Support (Scored)
def control_1_22_ensure_incident_management_roles():
    """Summary

    Returns:
        TYPE: Description
    """
    result = True
    failReason = ""
    offenders = []
    offenders_links = []
    control = "1.22"
    description = "Ensure a support role has been created to manage incidents with AWS Support"
    scored = True
    offenders = []
    offenders_links = []
    try:
        response = IAM_CLIENT.list_entities_for_policy(PolicyArn='arn:aws:iam::aws:policy/AWSSupportAccess'
        )
        if (len(response['PolicyGroups']) + len(response['PolicyUsers']) + len(response['PolicyRoles'])) == 0:
            result = False
            failReason = "No user, group, or role assigned AWSSupportAccess"
    except:
        result = False
        failReason = "AWSSupportAccess policy not created"
    return {'Result': result, 'failReason': failReason, 'Offenders': offenders, 'OffendersLinks': offenders_links, 'ScoredControl': scored, 'Description': description, 'ControlId': control}


# 1.23 Do not setup access keys during initial user setup for all IAM users that have a console password (Not Scored)
def control_1_23_no_active_initial_access_keys_with_iam_user(credreport):
    """Summary

    Returns:
        TYPE: Description
    """
    result = True
    failReason = ""
    offenders = []
    offenders_links = []
    control = "1.23"
    description = "Do not setup access keys during initial user setup for all IAM users that have a console password"
    scored = False
    offenders = []
    offenders_links = []
    for n, _ in enumerate(credreport):
        if (credreport[n]['access_key_1_active'] or credreport[n]['access_key_2_active'] == 'true') and n > 0:
            response = IAM_CLIENT.list_access_keys(UserName=str(credreport[n]['user'])
            )
            for m in response['AccessKeyMetadata']:
                if re.sub(r"\s", "T", str(m['CreateDate'])) == credreport[n]['user_creation_time']:
                    result = False
                    failReason = "Users with keys created at user creation time found"
                    offenders.append(str(credreport[n]['arn']) + ":" + str(m['AccessKeyId']))
                    offenders_links.append('https://console.aws.amazon.com/iam/home#/users/{user}?section=security_credentials'.format(user=credreport[n]['user']))
    return {'Result': result, 'failReason': failReason, 'Offenders': offenders, 'OffendersLinks': offenders_links, 'ScoredControl': scored, 'Description': description, 'ControlId': control}


# 1.24  Ensure IAM policies that allow full "*:*" administrative privileges are not created (Scored)
def control_1_24_no_overly_permissive_policies():
    """Summary

    Returns:
        TYPE: Description
    """
    result = True
    failReason = ""
    offenders = []
    offenders_links = []
    control = "1.24"
    description = "Ensure IAM policies that allow full administrative privileges are not created"
    scored = True
    offenders = []
    offenders_links = []
    paginator = IAM_CLIENT.get_paginator('list_policies')
    response_iterator = paginator.paginate(Scope='Local',
        OnlyAttached=False,
    )
    pagedResult = []
    for page in response_iterator:
        for n in page['Policies']:
            pagedResult.append(n)
    for m in pagedResult:
        policy = IAM_CLIENT.get_policy_version(PolicyArn=m['Arn'],
            VersionId=m['DefaultVersionId']
        )

        statements = []
        # a policy may contain a single statement, a single statement in an array, or multiple statements in an array
        if isinstance(policy['PolicyVersion']['Document']['Statement'], list):
            for statement in policy['PolicyVersion']['Document']['Statement']:
                statements.append(statement)
        else:
            statements.append(policy['PolicyVersion']['Document']['Statement'])

        for n in statements:
            # a policy statement has to contain either an Action or a NotAction
            if 'Action' in n.keys() and n['Effect'] == 'Allow':
                if ("*" in str(n['Action']) or str(n['Action']) == "*") and ("*" in str(n['Resource']) or str(n['Resource']) == "*"):
                    result = False
                    failReason = "Found full administrative policy"
                    offenders.append(str(m['Arn']))
                    offenders_links.append('https://console.aws.amazon.com/iam/home?#/policies/{policy_arn}'.format(policy_arn=m['Arn']))
    return {'Result': result, 'failReason': failReason, 'Offenders': offenders, 'OffendersLinks': offenders_links, 'ScoredControl': scored, 'Description': description, 'ControlId': control}


# --- 2 Logging ---

# 2.1 Ensure CloudTrail is enabled in all regions (Scored)
def control_2_1_ensure_cloud_trail_all_regions(cloudtrails):
    """Summary

    Args:
        cloudtrails (TYPE): Description

    Returns:
        TYPE: Description
    """
    result = False
    failReason = ""
    offenders = []
    offenders_links = []
    control = "2.1"
    description = "Ensure CloudTrail is enabled in all regions"
    scored = True
    for m, n in cloudtrails.iteritems():
        for o in n:
            if o['IsMultiRegionTrail']:
                client = boto3.client('cloudtrail', region_name=m)
                response = client.get_trail_status(Name=o['TrailARN']
                )
                if response['IsLogging'] is True:
                    result = True
                    break
    if result is False:
        failReason = "No enabled multi region trails found"
        offenders.append('Account')
        offenders_links.append('https://console.aws.amazon.com/cloudtrail/home#/configuration')
    return {'Result': result, 'failReason': failReason, 'Offenders': offenders, 'OffendersLinks': offenders_links, 'ScoredControl': scored, 'Description': description, 'ControlId': control}


# 2.2 Ensure CloudTrail log file validation is enabled (Scored)
def control_2_2_ensure_cloudtrail_validation(cloudtrails):
    """Summary

    Args:
        cloudtrails (TYPE): Description

    Returns:
        TYPE: Description
    """
    result = True
    failReason = ""
    offenders = []
    offenders_links = []
    control = "2.2"
    description = "Ensure CloudTrail log file validation is enabled"
    scored = True
    for m, n in cloudtrails.iteritems():
        for o in n:
            # The console uses an @ as part of the ARN.
            cloudtrail_arn_ui = o['TrailARN'].replace('/' + o['Name'], '@' + o['Name'])

            if o['LogFileValidationEnabled'] is False:
                result = False
                failReason = "CloudTrails without log file validation discovered"
                offenders.append(str(o['TrailARN']))
                offenders_links.append('https://console.aws.amazon.com/cloudtrail/home?region={region}#/configuration/{arn}'.format(
                    region=o['TrailARN'].split(':')[3],
                    arn=cloudtrail_arn_ui
                ))
    offenders = set(offenders)
    offenders = list(offenders)
    return {'Result': result, 'failReason': failReason, 'Offenders': offenders, 'OffendersLinks': offenders_links, 'ScoredControl': scored, 'Description': description, 'ControlId': control}


# 2.3 Ensure the S3 bucket CloudTrail logs to is not publicly accessible (Scored)
def control_2_3_ensure_cloudtrail_bucket_not_public(cloudtrails):
    """Summary

    Args:
        cloudtrails (TYPE): Description

    Returns:
        TYPE: Description
    """
    result = True
    failReason = ""
    offenders = []
    offenders_links = []
    control = "2.3"
    description = "Ensure the S3 bucket CloudTrail logs to is not publicly accessible"
    scored = True
    for m, n in cloudtrails.iteritems():
        for o in n:
            # The console uses an @ as part of the ARN.
            cloudtrail_arn_ui = o['TrailARN'].replace('/' + o['Name'], '@' + o['Name'])

            #  We only want to check cases where there is a bucket
            if "S3BucketName" in str(o):
                try:
                    response = S3_CLIENT.get_bucket_acl(Bucket=o['S3BucketName'])
                    for p in response['Grants']:
                        # print("Grantee is " + str(p['Grantee']))
                        if re.search(r'(global/AllUsers|global/AuthenticatedUsers)', str(p['Grantee'])):
                            result = False
                            offenders.append(str(o['TrailARN']) + ":PublicBucket")
                            offenders_links.append('https://s3.console.aws.amazon.com/s3/buckets/{bucket_name}/?tab=permissions'.format(bucket_name=o['S3BucketName']))
                            if "Publically" not in failReason:
                                failReason = failReason + "Publically accessible CloudTrail bucket discovered."
                except Exception as e:
                    result = False
                    if "AccessDenied" in str(e):
                        offenders.append(str(o['TrailARN']) + ":AccessDenied")
                        offenders_links.append('https://s3.console.aws.amazon.com/s3/buckets/{bucket_name}/?tab=permissions'.format(bucket_name=o['S3BucketName']))
                        if "Missing" not in failReason:
                            failReason = "Missing permissions to verify bucket ACL. " + failReason
                    elif "NoSuchBucket" in str(e):
                        offenders.append(str(o['TrailARN']) + ":NoBucket")
                        offenders_links.append('https://console.aws.amazon.com/cloudtrail/home?region={region}#/configuration/{arn}'.format(
                            region=o['TrailARN'].split(':')[3],
                            arn=cloudtrail_arn_ui
                        ))

                        if "Trailbucket" not in failReason:
                            failReason = "Trail bucket doesn't exist. " + failReason
                    else:
                        offenders.append(str(o['TrailARN']) + ":CannotVerify")
                        offenders_links.append('https://console.aws.amazon.com/cloudtrail/home?region={region}#/configuration/{arn}'.format(
                            region=o['TrailARN'].split(':')[3],
                            arn=cloudtrail_arn_ui
                        ))
                        if "Cannot" not in failReason:
                            failReason = "Cannot verify bucket ACL. " + failReason
            else:
                result = False
                offenders.append(str(o['TrailARN']) + "NoS3Logging")
                offenders_links.append('https://console.aws.amazon.com/cloudtrail/home?region={region}#/configuration/{arn}'.format(
                    region=o['TrailARN'].split(':')[3],
                    arn=cloudtrail_arn_ui
                ))
                failReason = "Cloudtrail not configured to log to S3. " + failReason
    return {'Result': result, 'failReason': failReason, 'Offenders': offenders, 'OffendersLinks': offenders_links, 'ScoredControl': scored, 'Description': description, 'ControlId': control}


# 2.4 Ensure CloudTrail trails are integrated with CloudWatch Logs (Scored)
def control_2_4_ensure_cloudtrail_cloudwatch_logs_integration(cloudtrails):
    """Summary

    Args:
        cloudtrails (TYPE): Description

    Returns:
        TYPE: Description
    """
    result = True
    failReason = ""
    offenders = []
    offenders_links = []
    control = "2.4"
    description = "Ensure CloudTrail trails are integrated with CloudWatch Logs"
    scored = True
    for m, n in cloudtrails.iteritems():
        for o in n:
            try:
                # The console uses an @ as part of the ARN.
                cloudtrail_arn_ui = o['TrailARN'].replace('/' + o['Name'], '@' + o['Name'])

                if "arn:aws:logs" in o['CloudWatchLogsLogGroupArn']:
                    pass
                else:
                    result = False
                    failReason = "CloudTrails without CloudWatch Logs discovered"
                    offenders.append(str(o['TrailARN']))
                    offenders_links.append('https://console.aws.amazon.com/cloudtrail/home?region={region}#/configuration/{arn}'.format(
                        region=o['TrailARN'].split(':')[3],
                        arn=cloudtrail_arn_ui
                    ))
            except:
                result = False
                failReason = "CloudTrails without CloudWatch Logs discovered"
                offenders.append(str(o['TrailARN']))
                offenders_links.append('https://console.aws.amazon.com/cloudtrail/home?region={region}#/configuration/{arn}'.format(
                    region=o['TrailARN'].split(':')[3],
                    arn=cloudtrail_arn_ui
                ))
    return {'Result': result, 'failReason': failReason, 'Offenders': offenders, 'OffendersLinks': offenders_links, 'ScoredControl': scored, 'Description': description, 'ControlId': control}


# 2.5 Ensure AWS Config is enabled in all regions (Scored)
def control_2_5_ensure_config_all_regions(regions):
    """Summary

    Returns:
        TYPE: Description
    """
    result = True
    failReason = ""
    offenders = []
    offenders_links = []
    control = "2.5"
    description = "Ensure AWS Config is enabled in all regions"
    scored = True
    globalConfigCapture = False  # Only one region needs to capture global events
    for n in regions:
        configClient = boto3.client('config', region_name=n)
        response = configClient.describe_configuration_recorder_status()
        # Get recording status
        try:
            if not response['ConfigurationRecordersStatus'][0]['recording'] is True:
                result = False
                failReason = "Config not enabled in all regions, not capturing all/global events or delivery channel errors"
                offenders.append(str(n) + ":NotRecording")
                offenders_links.append('https://console.aws.amazon.com/config/home?region={region}#/configure'.format(region=n))
        except:
            result = False
            failReason = "Config not enabled in all regions, not capturing all/global events or delivery channel errors"
            offenders.append(str(n) + ":NotRecording")
            offenders_links.append('https://console.aws.amazon.com/config/home?region={region}#/configure'.format(region=n))

        # Verify that each region is capturing all events
        response = configClient.describe_configuration_recorders()
        try:
            if not response['ConfigurationRecorders'][0]['recordingGroup']['allSupported'] is True:
                result = False
                failReason = "Config not enabled in all regions, not capturing all/global events or delivery channel errors"
                offenders.append(str(n) + ":NotAllEvents")
                offenders_links.append('https://console.aws.amazon.com/config/home?region={region}#/configure'.format(region=n))
        except:
            # This indicates that Config is disabled in the region and will be captured above.
            pass

        # Check if region is capturing global events. Fail is verified later since only one region needs to capture them.
        try:
            if response['ConfigurationRecorders'][0]['recordingGroup']['includeGlobalResourceTypes'] is True:
                globalConfigCapture = True
        except:
            pass

        # Verify the delivery channels
        response = configClient.describe_delivery_channel_status()
        try:
            if response['DeliveryChannelsStatus'][0]['configHistoryDeliveryInfo']['lastStatus'] != "SUCCESS":
                result = False
                failReason = "Config not enabled in all regions, not capturing all/global events or delivery channel errors"
                offenders.append(str(n) + ":S3orSNSDelivery")
                offenders_links.append('https://console.aws.amazon.com/config/home?region={region}#/configure'.format(region=n))
        except:
            pass  # Will be captured by earlier rule
        try:
            if response['DeliveryChannelsStatus'][0]['configStreamDeliveryInfo']['lastStatus'] != "SUCCESS":
                result = False
                failReason = "Config not enabled in all regions, not capturing all/global events or delivery channel errors"
                offenders.append(str(n) + ":SNSDelivery")
                offenders_links.append('https://console.aws.amazon.com/config/home?region={region}#/configure'.format(region=n))
        except:
            pass  # Will be captured by earlier rule

    # Verify that global events is captured by any region
    if globalConfigCapture is False:
        result = False
        failReason = "Config not enabled in all regions, not capturing all/global events or delivery channel errors"
        offenders.append("Global:NotRecording")
        offenders_links.append('https://console.aws.amazon.com/config/home#/configure')
    return {'Result': result, 'failReason': failReason, 'Offenders': offenders, 'OffendersLinks': offenders_links, 'ScoredControl': scored, 'Description': description, 'ControlId': control}


# 2.6 Ensure S3 bucket access logging is enabled on the CloudTrail S3 bucket (Scored)
def control_2_6_ensure_cloudtrail_bucket_logging(cloudtrails):
    """Summary

    Args:
        cloudtrails (TYPE): Description

    Returns:
        TYPE: Description
    """
    result = True
    failReason = ""
    offenders = []
    offenders_links = []
    control = "2.6"
    description = "Ensure S3 bucket access logging is enabled on the CloudTrail S3 bucket"
    scored = True
    for m, n in cloudtrails.iteritems():
        for o in n:
            # The console uses an @ as part of the ARN.
            cloudtrail_arn_ui = o['TrailARN'].replace('/' + o['Name'], '@' + o['Name'])

            # it is possible to have a cloudtrail configured with a nonexistant bucket
            try:
                response = S3_CLIENT.get_bucket_logging(Bucket=o['S3BucketName'])
            except:
                result = False
                failReason = "Cloudtrail not configured to log to S3. "
                offenders.append(str(o['TrailARN']))
                offenders_links.append('https://console.aws.amazon.com/cloudtrail/home?region={region}#/configuration/{arn}'.format(
                    region=o['TrailARN'].split(':')[3],
                    arn=cloudtrail_arn_ui
                ))
            try:
                if response['LoggingEnabled']:
                    pass
            except:
                result = False
                failReason = failReason + "CloudTrail S3 bucket without logging discovered"
                offenders.append("Trail:" + str(o['TrailARN']))
                offenders_links.append('https://s3.console.aws.amazon.com/s3/buckets/{bucket_name}/?tab=properties'.format(bucket_name=o['S3BucketName']))
    return {'Result': result, 'failReason': failReason, 'Offenders': offenders, 'OffendersLinks': offenders_links, 'ScoredControl': scored, 'Description': description, 'ControlId': control}


# 2.7 Ensure CloudTrail logs are encrypted at rest using KMS CMKs (Scored)
def control_2_7_ensure_cloudtrail_encryption_kms(cloudtrails):
    """Summary

    Args:
        cloudtrails (TYPE): Description

    Returns:
        TYPE: Description
    """
    result = True
    failReason = ""
    offenders = []
    offenders_links = []
    control = "2.7"
    description = "Ensure CloudTrail logs are encrypted at rest using KMS CMKs"
    scored = True
    for m, n in cloudtrails.iteritems():
        for o in n:
            # The console uses an @ as part of the ARN.
            cloudtrail_arn_ui = o['TrailARN'].replace('/' + o['Name'], '@' + o['Name'])

            try:
                if o['KmsKeyId']:
                    pass
            except:
                result = False
                failReason = "CloudTrail not using KMS CMK for encryption discovered"
                offenders.append("Trail:" + str(o['TrailARN']))
                offenders_links.append('https://console.aws.amazon.com/cloudtrail/home?region={region}#/configuration/{arn}'.format(
                    region=o['TrailARN'].split(':')[3],
                    arn=cloudtrail_arn_ui
                ))
    return {'Result': result, 'failReason': failReason, 'Offenders': offenders, 'OffendersLinks': offenders_links, 'ScoredControl': scored, 'Description': description, 'ControlId': control}


# 2.8 Ensure rotation for customer created CMKs is enabled (Scored)
def control_2_8_ensure_kms_cmk_rotation(regions):
    """Summary

    Returns:
        TYPE: Description
    """
    result = True
    failReason = ""
    offenders = []
    offenders_links = []
    control = "2.8"
    description = "Ensure rotation for customer created CMKs is enabled"
    scored = True
    for n in regions:
        kms_client = boto3.client('kms', region_name=n)
        paginator = kms_client.get_paginator('list_keys')
        response_iterator = paginator.paginate()
        for page in response_iterator:
            for n in page['Keys']:
                try:
                    rotationStatus = kms_client.get_key_rotation_status(KeyId=n['KeyId'])
                    if rotationStatus['KeyRotationEnabled'] is False:
                        keyDescription = kms_client.describe_key(KeyId=n['KeyId'])
                        # Ignore service keys
                        if "Default master key that protects my" not in str(keyDescription['KeyMetadata']['Description']):
                            result = False
                            failReason = "KMS CMK rotation not enabled"
                            offenders.append("Key:" + str(keyDescription['KeyMetadata']['Arn']))
                            offenders_links.append('https://console.aws.amazon.com/iam/home#/encryptionKeys/{key_arn}'.format(key_arn=n['Arn']))
                except:
                    pass  # Ignore keys without permission, for example ACM key
    return {'Result': result, 'failReason': failReason, 'Offenders': offenders, 'OffendersLinks': offenders_links, 'ScoredControl': scored, 'Description': description, 'ControlId': control}


# --- Monitoring ---

# 3.1 Ensure a log metric filter and alarm exist for unauthorized API calls (Scored)
def control_3_1_ensure_log_metric_filter_unauthorized_api_calls(cloudtrails):
    """Summary

    Returns:
        TYPE: Description
    """
    result = False
    failReason = ""
    offenders = []
    offenders_links = []
    control = "3.1"
    description = "Ensure log metric filter unauthorized api calls"
    scored = True
    failReason = "Incorrect log metric alerts for unauthorized_api_calls"
    for m, n in cloudtrails.iteritems():
        for o in n:
            try:
                if o['CloudWatchLogsLogGroupArn']:
                    group = re.search('log-group:(.+?):', o['CloudWatchLogsLogGroupArn']).group(1)
                    client = boto3.client('logs', region_name=m)
                    filters = client.describe_metric_filters(logGroupName=group)
                    for p in filters['metricFilters']:
                        patterns = ["\$\.errorCode\s*=\s*\"?\*UnauthorizedOperation(\"|\)|\s)", "\$\.errorCode\s*=\s*\"?AccessDenied\*(\"|\)|\s)"]
                        if find_in_string(patterns, str(p['filterPattern'])):
                            cwclient = boto3.client('cloudwatch', region_name=m)
                            response = cwclient.describe_alarms_for_metric(MetricName=p['metricTransformations'][0]['metricName'],
                                Namespace=p['metricTransformations'][0]['metricNamespace']
                            )
                            snsClient = boto3.client('sns', region_name=m)
                            subscribers = snsClient.list_subscriptions_by_topic(TopicArn=response['MetricAlarms'][0]['AlarmActions'][0]
                                #  Pagination not used since only 1 subscriber required
                            )
                            if not len(subscribers['Subscriptions']) == 0:
                                result = True
            except:
                pass
    return {'Result': result, 'failReason': failReason, 'Offenders': offenders, 'OffendersLinks': offenders_links, 'ScoredControl': scored, 'Description': description, 'ControlId': control}


# 3.2 Ensure a log metric filter and alarm exist for Management Console sign-in without MFA (Scored)
def control_3_2_ensure_log_metric_filter_console_signin_no_mfa(cloudtrails):
    """Summary

    Returns:
        TYPE: Description
    """
    result = False
    failReason = ""
    offenders = []
    offenders_links = []
    control = "3.2"
    description = "Ensure a log metric filter and alarm exist for Management Console sign-in without MFA"
    scored = True
    failReason = "Incorrect log metric alerts for management console signin without MFA"
    for m, n in cloudtrails.iteritems():
        for o in n:
            try:
                if o['CloudWatchLogsLogGroupArn']:
                    group = re.search('log-group:(.+?):', o['CloudWatchLogsLogGroupArn']).group(1)
                    client = boto3.client('logs', region_name=m)
                    filters = client.describe_metric_filters(logGroupName=group
                    )
                    for p in filters['metricFilters']:
                        patterns = ["\$\.eventName\s*=\s*\"?ConsoleLogin(\"|\)|\s)", "\$\.additionalEventData\.MFAUsed\s*\!=\s*\"?Yes"]
                        if find_in_string(patterns, str(p['filterPattern'])):
                            cwclient = boto3.client('cloudwatch', region_name=m)
                            response = cwclient.describe_alarms_for_metric(MetricName=p['metricTransformations'][0]['metricName'],
                                Namespace=p['metricTransformations'][0]['metricNamespace']
                            )
                            snsClient = boto3.client('sns', region_name=m)
                            subscribers = snsClient.list_subscriptions_by_topic(TopicArn=response['MetricAlarms'][0]['AlarmActions'][0]
                                #  Pagination not used since only 1 subscriber required
                            )
                            if not len(subscribers['Subscriptions']) == 0:
                                result = True
            except:
                pass
    return {'Result': result, 'failReason': failReason, 'Offenders': offenders, 'OffendersLinks': offenders_links, 'ScoredControl': scored, 'Description': description, 'ControlId': control}


# 3.3 Ensure a log metric filter and alarm exist for usage of "root" account (Scored)
def control_3_3_ensure_log_metric_filter_root_usage(cloudtrails):
    """Summary

    Returns:
        TYPE: Description
    """
    result = False
    failReason = ""
    offenders = []
    offenders_links = []
    control = "3.3"
    description = "Ensure a log metric filter and alarm exist for root usage"
    scored = True
    failReason = "Incorrect log metric alerts for root usage"
    for m, n in cloudtrails.iteritems():
        for o in n:
            try:
                if o['CloudWatchLogsLogGroupArn']:
                    group = re.search('log-group:(.+?):', o['CloudWatchLogsLogGroupArn']).group(1)
                    client = boto3.client('logs', region_name=m)
                    filters = client.describe_metric_filters(logGroupName=group
                    )
                    for p in filters['metricFilters']:
                        patterns = ["\$\.userIdentity\.type\s*=\s*\"?Root", "\$\.userIdentity\.invokedBy\s*NOT\s*EXISTS","\$\.eventType\s*\!=\s*\"?AwsServiceEvent(\"|\)|\s)"]
                        if find_in_string(patterns, str(p['filterPattern'])):
                            cwclient = boto3.client('cloudwatch', region_name=m)
                            response = cwclient.describe_alarms_for_metric(MetricName=p['metricTransformations'][0]['metricName'],
                                Namespace=p['metricTransformations'][0]['metricNamespace']
                            )
                            snsClient = boto3.client('sns', region_name=m)
                            subscribers = snsClient.list_subscriptions_by_topic(TopicArn=response['MetricAlarms'][0]['AlarmActions'][0]
                                #  Pagination not used since only 1 subscriber required
                            )
                            if not len(subscribers['Subscriptions']) == 0:
                                result = True
            except:
                pass
    return {'Result': result, 'failReason': failReason, 'Offenders': offenders, 'OffendersLinks': offenders_links, 'ScoredControl': scored, 'Description': description, 'ControlId': control}


# 3.4 Ensure a log metric filter and alarm exist for IAM policy changes  (Scored)
def control_3_4_ensure_log_metric_iam_policy_change(cloudtrails):
    """Summary

    Returns:
        TYPE: Description
    """
    result = False
    failReason = ""
    offenders = []
    offenders_links = []
    control = "3.4"
    description = "Ensure a log metric filter and alarm exist for IAM changes"
    scored = True
    failReason = "Incorrect log metric alerts for IAM policy changes"
    for m, n in cloudtrails.iteritems():
        for o in n:
            try:
                if o['CloudWatchLogsLogGroupArn']:
                    group = re.search('log-group:(.+?):', o['CloudWatchLogsLogGroupArn']).group(1)
                    client = boto3.client('logs', region_name=m)
                    filters = client.describe_metric_filters(logGroupName=group
                    )
                    for p in filters['metricFilters']:
                        patterns = ["\$\.eventName\s*=\s*\"?DeleteGroupPolicy(\"|\)|\s)", "\$\.eventName\s*=\s*\"?DeleteRolePolicy(\"|\)|\s)", "\$\.eventName\s*=\s*\"?DeleteUserPolicy(\"|\)|\s)", "\$\.eventName\s*=\s*\"?PutGroupPolicy(\"|\)|\s)", "\$\.eventName\s*=\s*\"?PutRolePolicy(\"|\)|\s)", "\$\.eventName\s*=\s*\"?PutUserPolicy(\"|\)|\s)", "\$\.eventName\s*=\s*\"?CreatePolicy(\"|\)|\s)", "\$\.eventName\s*=\s*\"?DeletePolicy(\"|\)|\s)","\$\.eventName\s*=\s*\"?CreatePolicyVersion(\"|\)|\s)", "\$\.eventName\s*=\s*\"?DeletePolicyVersion(\"|\)|\s)", "\$\.eventName\s*=\s*\"?AttachRolePolicy(\"|\)|\s)", "\$\.eventName\s*=\s*\"?DetachRolePolicy(\"|\)|\s)", "\$\.eventName\s*=\s*\"?AttachUserPolicy(\"|\)|\s)", "\$\.eventName\s*=\s*\"?DetachUserPolicy(\"|\)|\s)", "\$\.eventName\s*=\s*\"?AttachGroupPolicy(\"|\)|\s)", "\$\.eventName\s*=\s*\"?DetachGroupPolicy(\"|\)|\s)"]
                        if find_in_string(patterns, str(p['filterPattern'])):
                            cwclient = boto3.client('cloudwatch', region_name=m)
                            response = cwclient.describe_alarms_for_metric(MetricName=p['metricTransformations'][0]['metricName'],
                                Namespace=p['metricTransformations'][0]['metricNamespace']
                            )
                            snsClient = boto3.client('sns', region_name=m)
                            subscribers = snsClient.list_subscriptions_by_topic(TopicArn=response['MetricAlarms'][0]['AlarmActions'][0]
                                #  Pagination not used since only 1 subscriber required
                            )
                            if not len(subscribers['Subscriptions']) == 0:
                                result = True
            except:
                pass
    return {'Result': result, 'failReason': failReason, 'Offenders': offenders, 'OffendersLinks': offenders_links, 'ScoredControl': scored, 'Description': description, 'ControlId': control}


# 3.5 Ensure a log metric filter and alarm exist for CloudTrail configuration changes (Scored)
def control_3_5_ensure_log_metric_cloudtrail_configuration_changes(cloudtrails):
    """Summary

    Returns:
        TYPE: Description
    """
    result = False
    failReason = ""
    offenders = []
    offenders_links = []
    control = "3.5"
    description = "Ensure a log metric filter and alarm exist for CloudTrail configuration changes"
    scored = True
    failReason = "Incorrect log metric alerts for CloudTrail configuration changes"
    for m, n in cloudtrails.iteritems():
        for o in n:
            try:
                if o['CloudWatchLogsLogGroupArn']:
                    group = re.search('log-group:(.+?):', o['CloudWatchLogsLogGroupArn']).group(1)
                    client = boto3.client('logs', region_name=m)
                    filters = client.describe_metric_filters(logGroupName=group
                    )
                    for p in filters['metricFilters']:
                        patterns = ["\$\.eventName\s*=\s*\"?CreateTrail(\"|\)|\s)", "\$\.eventName\s*=\s*\"?UpdateTrail(\"|\)|\s)", "\$\.eventName\s*=\s*\"?DeleteTrail(\"|\)|\s)","\$\.eventName\s*=\s*\"?StartLogging(\"|\)|\s)", "\$\.eventName\s*=\s*\"?StopLogging(\"|\)|\s)"]
                        if find_in_string(patterns, str(p['filterPattern'])):
                            cwclient = boto3.client('cloudwatch', region_name=m)
                            response = cwclient.describe_alarms_for_metric(MetricName=p['metricTransformations'][0]['metricName'],
                                Namespace=p['metricTransformations'][0]['metricNamespace']
                            )
                            snsClient = boto3.client('sns', region_name=m)
                            subscribers = snsClient.list_subscriptions_by_topic(TopicArn=response['MetricAlarms'][0]['AlarmActions'][0]
                                #  Pagination not used since only 1 subscriber required
                            )
                            if not len(subscribers['Subscriptions']) == 0:
                                result = True
            except:
                pass
    return {'Result': result, 'failReason': failReason, 'Offenders': offenders, 'OffendersLinks': offenders_links, 'ScoredControl': scored, 'Description': description, 'ControlId': control}


# 3.6 Ensure a log metric filter and alarm exist for AWS Management Console authentication failures (Scored)
def control_3_6_ensure_log_metric_console_auth_failures(cloudtrails):
    """Summary

    Returns:
        TYPE: Description
    """
    result = False
    failReason = ""
    offenders = []
    offenders_links = []
    control = "3.6"
    description = "Ensure a log metric filter and alarm exist for console auth failures"
    scored = True
    failReason = "Ensure a log metric filter and alarm exist for console auth failures"
    for m, n in cloudtrails.iteritems():
        for o in n:
            try:
                if o['CloudWatchLogsLogGroupArn']:
                    group = re.search('log-group:(.+?):', o['CloudWatchLogsLogGroupArn']).group(1)
                    client = boto3.client('logs', region_name=m)
                    filters = client.describe_metric_filters(logGroupName=group
                    )
                    for p in filters['metricFilters']:
                        patterns = ["\$\.eventName\s*=\s*\"?ConsoleLogin(\"|\)|\s)", "\$\.errorMessage\s*=\s*\"?Failed authentication(\"|\)|\s)"]
                        if find_in_string(patterns, str(p['filterPattern'])):
                            cwclient = boto3.client('cloudwatch', region_name=m)
                            response = cwclient.describe_alarms_for_metric(MetricName=p['metricTransformations'][0]['metricName'],
                                Namespace=p['metricTransformations'][0]['metricNamespace']
                            )
                            snsClient = boto3.client('sns', region_name=m)
                            subscribers = snsClient.list_subscriptions_by_topic(TopicArn=response['MetricAlarms'][0]['AlarmActions'][0]
                                #  Pagination not used since only 1 subscriber required
                            )
                            if not len(subscribers['Subscriptions']) == 0:
                                result = True
            except:
                pass
    return {'Result': result, 'failReason': failReason, 'Offenders': offenders, 'OffendersLinks': offenders_links, 'ScoredControl': scored, 'Description': description, 'ControlId': control}


# 3.7 Ensure a log metric filter and alarm exist for disabling or scheduled deletion of customer created CMKs (Scored)
def control_3_7_ensure_log_metric_disabling_scheduled_delete_of_kms_cmk(cloudtrails):
    """Summary

    Returns:
        TYPE: Description
    """
    result = False
    failReason = ""
    offenders = []
    offenders_links = []
    control = "3.7"
    description = "Ensure a log metric filter and alarm exist for disabling or scheduling deletion of KMS CMK"
    scored = True
    failReason = "Ensure a log metric filter and alarm exist for disabling or scheduling deletion of KMS CMK"
    for m, n in cloudtrails.iteritems():
        for o in n:
            try:
                if o['CloudWatchLogsLogGroupArn']:
                    group = re.search('log-group:(.+?):', o['CloudWatchLogsLogGroupArn']).group(1)
                    client = boto3.client('logs', region_name=m)
                    filters = client.describe_metric_filters(logGroupName=group)
                    for p in filters['metricFilters']:
                        patterns = ["\$\.eventSource\s*=\s*\"?kms\.amazonaws\.com(\"|\)|\s)", "\$\.eventName\s*=\s*\"?DisableKey(\"|\)|\s)", "\$\.eventName\s*=\s*\"?ScheduleKeyDeletion(\"|\)|\s)"]
                        if find_in_string(patterns, str(p['filterPattern'])):
                            cwclient = boto3.client('cloudwatch', region_name=m)
                            response = cwclient.describe_alarms_for_metric(MetricName=p['metricTransformations'][0]['metricName'],
                                Namespace=p['metricTransformations'][0]['metricNamespace']
                            )
                            snsClient = boto3.client('sns', region_name=m)
                            subscribers = snsClient.list_subscriptions_by_topic(TopicArn=response['MetricAlarms'][0]['AlarmActions'][0]
                                #  Pagination not used since only 1 subscriber required
                            )
                            if not len(subscribers['Subscriptions']) == 0:
                                result = True
            except:
                pass
    return {'Result': result, 'failReason': failReason, 'Offenders': offenders, 'OffendersLinks': offenders_links, 'ScoredControl': scored, 'Description': description, 'ControlId': control}


# 3.8 Ensure a log metric filter and alarm exist for S3 bucket policy changes (Scored)
def control_3_8_ensure_log_metric_s3_bucket_policy_changes(cloudtrails):
    """Summary

    Returns:
        TYPE: Description
    """
    result = False
    failReason = ""
    offenders = []
    offenders_links = []
    control = "3.8"
    description = "Ensure a log metric filter and alarm exist for S3 bucket policy changes"
    scored = True
    failReason = "Ensure a log metric filter and alarm exist for S3 bucket policy changes"
    for m, n in cloudtrails.iteritems():
        for o in n:
            try:
                if o['CloudWatchLogsLogGroupArn']:
                    group = re.search('log-group:(.+?):', o['CloudWatchLogsLogGroupArn']).group(1)
                    client = boto3.client('logs', region_name=m)
                    filters = client.describe_metric_filters(logGroupName=group
                    )
                    for p in filters['metricFilters']:
                        patterns = ["\$\.eventSource\s*=\s*\"?s3\.amazonaws\.com(\"|\)|\s)", "\$\.eventName\s*=\s*\"?PutBucketAcl(\"|\)|\s)", "\$\.eventName\s*=\s*\"?PutBucketPolicy(\"|\)|\s)", "\$\.eventName\s*=\s*\"?PutBucketCors(\"|\)|\s)", "\$\.eventName\s*=\s*\"?PutBucketLifecycle(\"|\)|\s)","\$\.eventName\s*=\s*\"?PutBucketReplication(\"|\)|\s)", "\$\.eventName\s*=\s*\"?DeleteBucketPolicy(\"|\)|\s)", "\$\.eventName\s*=\s*\"?DeleteBucketCors(\"|\)|\s)", "\$\.eventName\s*=\s*\"?DeleteBucketLifecycle(\"|\)|\s)", "\$\.eventName\s*=\s*\"?DeleteBucketReplication(\"|\)|\s)"]
                        if find_in_string(patterns, str(p['filterPattern'])):
                            cwclient = boto3.client('cloudwatch', region_name=m)
                            response = cwclient.describe_alarms_for_metric(MetricName=p['metricTransformations'][0]['metricName'],
                                Namespace=p['metricTransformations'][0]['metricNamespace']
                            )
                            snsClient = boto3.client('sns', region_name=m)
                            subscribers = snsClient.list_subscriptions_by_topic(TopicArn=response['MetricAlarms'][0]['AlarmActions'][0]
                                #  Pagination not used since only 1 subscriber required
                            )
                            if not len(subscribers['Subscriptions']) == 0:
                                result = True
            except:
                pass
    return {'Result': result, 'failReason': failReason, 'Offenders': offenders, 'OffendersLinks': offenders_links, 'ScoredControl': scored, 'Description': description, 'ControlId': control}


# 3.9 Ensure a log metric filter and alarm exist for AWS Config configuration changes (Scored)
def control_3_9_ensure_log_metric_config_configuration_changes(cloudtrails):
    """Summary

    Returns:
        TYPE: Description
    """
    result = False
    failReason = ""
    offenders = []
    offenders_links = []
    control = "3.9"
    description = "Ensure a log metric filter and alarm exist for for AWS Config configuration changes"
    scored = True
    failReason = "Ensure a log metric filter and alarm exist for for AWS Config configuration changes"
    for m, n in cloudtrails.iteritems():
        for o in n:
            try:
                if o['CloudWatchLogsLogGroupArn']:
                    group = re.search('log-group:(.+?):', o['CloudWatchLogsLogGroupArn']).group(1)
                    client = boto3.client('logs', region_name=m)
                    filters = client.describe_metric_filters(logGroupName=group)
                    for p in filters['metricFilters']:
                        patterns = ["\$\.eventSource\s*=\s*\"?config\.amazonaws\.com(\"|\)|\s)", "\$\.eventName\s*=\s*\"?StopConfigurationRecorder(\"|\)|\s)", "\$\.eventName\s*=\s*\"?DeleteDeliveryChannel(\"|\)|\s)","\$\.eventName\s*=\s*\"?PutDeliveryChannel(\"|\)|\s)", "\$\.eventName\s*=\s*\"?PutConfigurationRecorder(\"|\)|\s)"]
                        if find_in_string(patterns, str(p['filterPattern'])):
                            cwclient = boto3.client('cloudwatch', region_name=m)
                            response = cwclient.describe_alarms_for_metric(MetricName=p['metricTransformations'][0]['metricName'],
                                Namespace=p['metricTransformations'][0]['metricNamespace']
                            )
                            snsClient = boto3.client('sns', region_name=m)
                            subscribers = snsClient.list_subscriptions_by_topic(TopicArn=response['MetricAlarms'][0]['AlarmActions'][0]
                                #  Pagination not used since only 1 subscriber required
                            )
                            if not len(subscribers['Subscriptions']) == 0:
                                result = True
            except:
                pass
    return {'Result': result, 'failReason': failReason, 'Offenders': offenders, 'OffendersLinks': offenders_links, 'ScoredControl': scored, 'Description': description, 'ControlId': control}


# 3.10 Ensure a log metric filter and alarm exist for security group changes (Scored)
def control_3_10_ensure_log_metric_security_group_changes(cloudtrails):
    """Summary

    Returns:
        TYPE: Description
    """
    result = False
    failReason = ""
    offenders = []
    offenders_links = []
    control = "3.10"
    description = "Ensure a log metric filter and alarm exist for security group changes"
    scored = True
    failReason = "Ensure a log metric filter and alarm exist for security group changes"
    for m, n in cloudtrails.iteritems():
        for o in n:
            try:
                if o['CloudWatchLogsLogGroupArn']:
                    group = re.search('log-group:(.+?):', o['CloudWatchLogsLogGroupArn']).group(1)
                    client = boto3.client('logs', region_name=m)
                    filters = client.describe_metric_filters(logGroupName=group
                    )
                    for p in filters['metricFilters']:
                        patterns = ["\$\.eventName\s*=\s*\"?AuthorizeSecurityGroupIngress(\"|\)|\s)", "\$\.eventName\s*=\s*\"?AuthorizeSecurityGroupEgress(\"|\)|\s)", "\$\.eventName\s*=\s*\"?RevokeSecurityGroupIngress(\"|\)|\s)","\$\.eventName\s*=\s*\"?RevokeSecurityGroupEgress(\"|\)|\s)", "\$\.eventName\s*=\s*\"?CreateSecurityGroup(\"|\)|\s)", "\$\.eventName\s*=\s*\"?DeleteSecurityGroup(\"|\)|\s)"]
                        if find_in_string(patterns, str(p['filterPattern'])):
                            cwclient = boto3.client('cloudwatch', region_name=m)
                            response = cwclient.describe_alarms_for_metric(MetricName=p['metricTransformations'][0]['metricName'],
                                Namespace=p['metricTransformations'][0]['metricNamespace']
                            )
                            snsClient = boto3.client('sns', region_name=m)
                            subscribers = snsClient.list_subscriptions_by_topic(TopicArn=response['MetricAlarms'][0]['AlarmActions'][0]
                                #  Pagination not used since only 1 subscriber required
                            )
                            if not len(subscribers['Subscriptions']) == 0:
                                result = True
            except:
                pass
    return {'Result': result, 'failReason': failReason, 'Offenders': offenders, 'OffendersLinks': offenders_links, 'ScoredControl': scored, 'Description': description, 'ControlId': control}


# 3.11 Ensure a log metric filter and alarm exist for changes to Network Access Control Lists (NACL) (Scored)
def control_3_11_ensure_log_metric_nacl(cloudtrails):
    """Summary

    Returns:
        TYPE: Description
    """
    result = False
    failReason = ""
    offenders = []
    offenders_links = []
    control = "3.11"
    description = "Ensure a log metric filter and alarm exist for changes to Network Access Control Lists (NACL)"
    scored = True
    failReason = "Ensure a log metric filter and alarm exist for changes to Network Access Control Lists (NACL)"
    for m, n in cloudtrails.iteritems():
        for o in n:
            try:
                if o['CloudWatchLogsLogGroupArn']:
                    group = re.search('log-group:(.+?):', o['CloudWatchLogsLogGroupArn']).group(1)
                    client = boto3.client('logs', region_name=m)
                    filters = client.describe_metric_filters(logGroupName=group)
                    for p in filters['metricFilters']:
                        patterns = ["\$\.eventName\s*=\s*\"?CreateNetworkAcl(\"|\)|\s)", "\$\.eventName\s*=\s*\"?CreateNetworkAclEntry(\"|\)|\s)", "\$\.eventName\s*=\s*\"?DeleteNetworkAcl(\"|\)|\s)","\$\.eventName\s*=\s*\"?DeleteNetworkAclEntry(\"|\)|\s)", "\$\.eventName\s*=\s*\"?ReplaceNetworkAclEntry(\"|\)|\s)", "\$\.eventName\s*=\s*\"?ReplaceNetworkAclAssociation(\"|\)|\s)"]
                        if find_in_string(patterns, str(p['filterPattern'])):
                            cwclient = boto3.client('cloudwatch', region_name=m)
                            response = cwclient.describe_alarms_for_metric(MetricName=p['metricTransformations'][0]['metricName'],
                                Namespace=p['metricTransformations'][0]['metricNamespace']
                            )
                            snsClient = boto3.client('sns', region_name=m)
                            subscribers = snsClient.list_subscriptions_by_topic(TopicArn=response['MetricAlarms'][0]['AlarmActions'][0]
                                #  Pagination not used since only 1 subscriber required
                            )
                            if not len(subscribers['Subscriptions']) == 0:
                                result = True
            except:
                pass
    return {'Result': result, 'failReason': failReason, 'Offenders': offenders, 'OffendersLinks': offenders_links, 'ScoredControl': scored, 'Description': description, 'ControlId': control}


# 3.12 Ensure a log metric filter and alarm exist for changes to network gateways (Scored)
def control_3_12_ensure_log_metric_changes_to_network_gateways(cloudtrails):
    """Summary

    Returns:
        TYPE: Description
    """
    result = False
    failReason = ""
    offenders = []
    offenders_links = []
    control = "3.12"
    description = "Ensure a log metric filter and alarm exist for changes to network gateways"
    scored = True
    failReason = "Ensure a log metric filter and alarm exist for changes to network gateways"
    for m, n in cloudtrails.iteritems():
        for o in n:
            try:
                if o['CloudWatchLogsLogGroupArn']:
                    group = re.search('log-group:(.+?):', o['CloudWatchLogsLogGroupArn']).group(1)
                    client = boto3.client('logs', region_name=m)
                    filters = client.describe_metric_filters(logGroupName=group)
                    for p in filters['metricFilters']:
                        patterns = ["\$\.eventName\s*=\s*\"?CreateCustomerGateway(\"|\)|\s)", "\$\.eventName\s*=\s*\"?DeleteCustomerGateway(\"|\)|\s)", "\$\.eventName\s*=\s*\"?AttachInternetGateway(\"|\)|\s)","\$\.eventName\s*=\s*\"?CreateInternetGateway(\"|\)|\s)", "\$\.eventName\s*=\s*\"?DeleteInternetGateway(\"|\)|\s)", "\$\.eventName\s*=\s*\"?DetachInternetGateway(\"|\)|\s)"]
                        if find_in_string(patterns, str(p['filterPattern'])):
                            cwclient = boto3.client('cloudwatch', region_name=m)
                            response = cwclient.describe_alarms_for_metric(MetricName=p['metricTransformations'][0]['metricName'],
                                Namespace=p['metricTransformations'][0]['metricNamespace']
                            )
                            snsClient = boto3.client('sns', region_name=m)
                            subscribers = snsClient.list_subscriptions_by_topic(TopicArn=response['MetricAlarms'][0]['AlarmActions'][0]
                                #  Pagination not used since only 1 subscriber required
                            )
                            if not len(subscribers['Subscriptions']) == 0:
                                result = True
            except:
                pass
    return {'Result': result, 'failReason': failReason, 'Offenders': offenders, 'OffendersLinks': offenders_links, 'ScoredControl': scored, 'Description': description, 'ControlId': control}


# 3.13 Ensure a log metric filter and alarm exist for route table changes (Scored)
def control_3_13_ensure_log_metric_changes_to_route_tables(cloudtrails):
    """Summary

    Returns:
        TYPE: Description
    """
    result = False
    failReason = ""
    offenders = []
    offenders_links = []
    control = "3.13"
    description = "Ensure a log metric filter and alarm exist for route table changes"
    scored = True
    failReason = "Ensure a log metric filter and alarm exist for route table changes"
    for m, n in cloudtrails.iteritems():
        for o in n:
            try:
                if o['CloudWatchLogsLogGroupArn']:
                    group = re.search('log-group:(.+?):', o['CloudWatchLogsLogGroupArn']).group(1)
                    client = boto3.client('logs', region_name=m)
                    filters = client.describe_metric_filters(logGroupName=group)
                    for p in filters['metricFilters']:
                        patterns = ["\$\.eventName\s*=\s*\"?CreateRoute(\"|\)|\s)", "\$\.eventName\s*=\s*\"?CreateRouteTable(\"|\)|\s)", "\$\.eventName\s*=\s*\"?ReplaceRoute(\"|\)|\s)", "\$\.eventName\s*=\s*\"?ReplaceRouteTableAssociation(\"|\)|\s)","\$\.eventName\s*=\s*\"?DeleteRouteTable(\"|\)|\s)", "\$\.eventName\s*=\s*\"?DeleteRoute(\"|\)|\s)", "\$\.eventName\s*=\s*\"?DisassociateRouteTable(\"|\)|\s)"]
                        if find_in_string(patterns, str(p['filterPattern'])):
                            cwclient = boto3.client('cloudwatch', region_name=m)
                            response = cwclient.describe_alarms_for_metric(MetricName=p['metricTransformations'][0]['metricName'],
                                Namespace=p['metricTransformations'][0]['metricNamespace']
                            )
                            snsClient = boto3.client('sns', region_name=m)
                            subscribers = snsClient.list_subscriptions_by_topic(TopicArn=response['MetricAlarms'][0]['AlarmActions'][0]
                                #  Pagination not used since only 1 subscriber required
                            )
                            if not len(subscribers['Subscriptions']) == 0:
                                result = True
            except:
                pass
    return {'Result': result, 'failReason': failReason, 'Offenders': offenders, 'OffendersLinks': offenders_links, 'ScoredControl': scored, 'Description': description, 'ControlId': control}


# 3.14 Ensure a log metric filter and alarm exist for VPC changes (Scored)
def control_3_14_ensure_log_metric_changes_to_vpc(cloudtrails):
    """Summary

    Returns:
        TYPE: Description
    """
    result = False
    failReason = ""
    offenders = []
    offenders_links = []
    control = "3.14"
    description = "Ensure a log metric filter and alarm exist for VPC changes"
    scored = True
    failReason = "Ensure a log metric filter and alarm exist for VPC changes"
    for m, n in cloudtrails.iteritems():
        for o in n:
            try:
                if o['CloudWatchLogsLogGroupArn']:
                    group = re.search('log-group:(.+?):', o['CloudWatchLogsLogGroupArn']).group(1)
                    client = boto3.client('logs', region_name=m)
                    filters = client.describe_metric_filters(logGroupName=group)
                    for p in filters['metricFilters']:
                        patterns = ["\$\.eventName\s*=\s*\"?CreateVpc(\"|\)|\s)", "\$\.eventName\s*=\s*\"?DeleteVpc(\"|\)|\s)", "\$\.eventName\s*=\s*\"?ModifyVpcAttribute(\"|\)|\s)", "\$\.eventName\s*=\s*\"?AcceptVpcPeeringConnection(\"|\)|\s)", "\$\.eventName\s*=\s*\"?CreateVpcPeeringConnection(\"|\)|\s)", "\$\.eventName\s*=\s*\"?DeleteVpcPeeringConnection(\"|\)|\s)","\$\.eventName\s*=\s*\"?RejectVpcPeeringConnection(\"|\)|\s)", "\$\.eventName\s*=\s*\"?AttachClassicLinkVpc(\"|\)|\s)", "\$\.eventName\s*=\s*\"?DetachClassicLinkVpc(\"|\)|\s)", "\$\.eventName\s*=\s*\"?DisableVpcClassicLink(\"|\)|\s)", "\$\.eventName\s*=\s*\"?EnableVpcClassicLink(\"|\)|\s)"]
                        if find_in_string(patterns, str(p['filterPattern'])):
                            cwclient = boto3.client('cloudwatch', region_name=m)
                            response = cwclient.describe_alarms_for_metric(MetricName=p['metricTransformations'][0]['metricName'],
                                Namespace=p['metricTransformations'][0]['metricNamespace']
                            )
                            snsClient = boto3.client('sns', region_name=m)
                            subscribers = snsClient.list_subscriptions_by_topic(TopicArn=response['MetricAlarms'][0]['AlarmActions'][0]
                                #  Pagination not used since only 1 subscriber required
                            )
                            if not len(subscribers['Subscriptions']) == 0:
                                result = True
            except:
                pass
    return {'Result': result, 'failReason': failReason, 'Offenders': offenders, 'OffendersLinks': offenders_links, 'ScoredControl': scored, 'Description': description, 'ControlId': control}


# 3.15 Ensure appropriate subscribers to each SNS topic (Not Scored)
def control_3_15_verify_sns_subscribers():
    """Summary

    Returns:
        TYPE: Description
    """
    result = "Manual"
    failReason = ""
    offenders = []
    offenders_links = []
    control = "3.15"
    description = "Ensure appropriate subscribers to each SNS topic, please verify manually"
    scored = False
    failReason = "Control not implemented using API, please verify manually"
    return {'Result': result, 'failReason': failReason, 'Offenders': offenders, 'OffendersLinks': offenders_links, 'ScoredControl': scored, 'Description': description, 'ControlId': control}

# 3.14 Ensure a log metric filter and alarm exist for Organizations changes (Scored)
def control_3_16_ensure_log_metric_changes_to_organizations(cloudtrails):
    """Summary

    Returns:
        TYPE: Description
    """
    result = False
    failReason = ""
    offenders = []
    offenders_links = []
    control = "3.16"
    description = "Ensure a log metric filter and alarm exist for Organizations changes"
    scored = True
    failReason = "A log metric filter and alarm do not exist for Organizations changes"
    for m, n in cloudtrails.iteritems():
        for o in n:
            try:
                if o['CloudWatchLogsLogGroupArn']:
                    group = re.search('log-group:(.+?):', o['CloudWatchLogsLogGroupArn']).group(1)
                    client = boto3.client('logs', region_name=m)
                    filters = client.describe_metric_filters(logGroupName=group)
                    for p in filters['metricFilters']:
                        patterns = ["\$\.eventName\s*=\s*\"?CreateAccount(\"|\)|\s)", "\$\.eventName\s*=\s*\"?CreatePolicy(\"|\)|\s)", "\$\.eventName\s*=\s*\"?CreateOrganizationalUnit(\"|\)|\s)", "\$\.eventName\s*=\s*\"?DeleteOrganization(\"|\)|\s)", "\$\.eventName\s*=\s*\"?DeleteOrganizationalUnit(\"|\)|\s)", "\$\.eventName\s*=\s*\"?DeletePolicy(\"|\)|\s)", "\$\.eventName\s*=\s*\"?DetachPolicy(\"|\)|\s)","\$\.eventName\s*=\s*\"?DisableAWSServiceAccess(\"|\)|\s)", "\$\.eventName\s*=\s*\"?DisablePolicyType(\"|\)|\s)", "\$\.eventName\s*=\s*\"?MoveAccount(\"|\)|\s)", "\$\.eventName\s*=\s*\"?RemoveAccountFromOrganization(\"|\)|\s)", "\$\.eventName\s*=\s*\"?UpdateOrganizationalUnit(\"|\)|\s)", "\$\.eventName\s*=\s*\"?UpdatePolicy(\"|\)|\s)"]
                        if find_in_string(patterns, str(p['filterPattern'])):
                            cwclient = boto3.client('cloudwatch', region_name=m)
                            response = cwclient.describe_alarms_for_metric(MetricName=p['metricTransformations'][0]['metricName'],
                                Namespace=p['metricTransformations'][0]['metricNamespace']
                            )
                            snsClient = boto3.client('sns', region_name=m)
                            subscribers = snsClient.list_subscriptions_by_topic(TopicArn=response['MetricAlarms'][0]['AlarmActions'][0]
                                #  Pagination not used since only 1 subscriber required
                            )
                            if not len(subscribers['Subscriptions']) == 0:
                                result = True
            except:
                pass
    return {'Result': result, 'failReason': failReason, 'Offenders': offenders, 'OffendersLinks': offenders_links, 'ScoredControl': scored, 'Description': description, 'ControlId': control}



# --- Networking ---

# 4.1 Ensure no security groups allow ingress from 0.0.0.0/0 to port 22 (Scored)
def control_4_1_ensure_ssh_not_open_to_world(regions):
    """Summary

    Returns:
        TYPE: Description
    """
    result = True
    failReason = ""
    offenders = []
    offenders_links = []
    control = "4.1"
    description = "Ensure no security groups allow ingress from 0.0.0.0/0 to port 22"
    scored = True
    for n in regions:
        client = boto3.client('ec2', region_name=n)
        response = client.describe_security_groups()
        for m in response['SecurityGroups']:
            if "0.0.0.0/0" in str(m['IpPermissions']):
                for o in m['IpPermissions']:
                    try:
                        if int(o['FromPort']) <= 22 <= int(o['ToPort']) and '0.0.0.0/0' in str(o['IpRanges']):
                            result = False
                            failReason = "Found Security Group with port 22 open to the world (0.0.0.0/0)"
                            offenders.append(str(m['GroupId']))
                            offenders_links.append('https://console.aws.amazon.com/ec2/v2/home?region={region}#SecurityGroups:search={security_group}'.format(
                                region=n,
                                security_group=m['GroupId']
                            ))
                    except:
                        if str(o['IpProtocol']) == "-1" and '0.0.0.0/0' in str(o['IpRanges']):
                            result = False
                            failReason = "Found Security Group with port 22 open to the world (0.0.0.0/0)"
                            offenders.append(str(n) + " : " + str(m['GroupId']))
                            offenders_links.append('https://console.aws.amazon.com/ec2/v2/home?region={region}#SecurityGroups:search={security_group}'.format(
                                region=n,
                                security_group=m['GroupId']
                            ))
    return {'Result': result, 'failReason': failReason, 'Offenders': offenders, 'OffendersLinks': offenders_links, 'ScoredControl': scored, 'Description': description, 'ControlId': control}


# 4.2 Ensure no security groups allow ingress from 0.0.0.0/0 to port 3389 (Scored)
def control_4_2_ensure_rdp_not_open_to_world(regions):
    """Summary

    Returns:
        TYPE: Description
    """
    result = True
    failReason = ""
    offenders = []
    offenders_links = []
    control = "4.2"
    description = "Ensure no security groups allow ingress from 0.0.0.0/0 to port 3389"
    scored = True
    for n in regions:
        client = boto3.client('ec2', region_name=n)
        response = client.describe_security_groups()
        for m in response['SecurityGroups']:
            if "0.0.0.0/0" in str(m['IpPermissions']):
                for o in m['IpPermissions']:
                    try:
                        if int(o['FromPort']) <= 3389 <= int(o['ToPort']) and '0.0.0.0/0' in str(o['IpRanges']):
                            result = False
                            failReason = "Found Security Group with port 3389 open to the world (0.0.0.0/0)"
                            offenders.append(str(m['GroupId']))
                            offenders_links.append('https://console.aws.amazon.com/ec2/v2/home?region={region}#SecurityGroups:search={security_group}'.format(
                                region=n,
                                security_group=m['GroupId']
                            ))
                    except:
                        if str(o['IpProtocol']) == "-1" and '0.0.0.0/0' in str(o['IpRanges']):
                            result = False
                            failReason = "Found Security Group with port 3389 open to the world (0.0.0.0/0)"
                            offenders.append(str(n) + " : " + str(m['GroupId']))
                            offenders_links.append('https://console.aws.amazon.com/ec2/v2/home?region={region}#SecurityGroups:search={security_group}'.format(
                                region=n,
                                security_group=m['GroupId']
                            ))
    return {'Result': result, 'failReason': failReason, 'Offenders': offenders, 'OffendersLinks': offenders_links, 'ScoredControl': scored, 'Description': description, 'ControlId': control}


# 4.3 Ensure VPC flow logging is enabled in all VPCs (Scored)
def control_4_3_ensure_flow_logs_enabled_on_all_vpc(regions):
    """Summary

    Returns:
        TYPE: Description
    """
    result = True
    failReason = ""
    offenders = []
    offenders_links = []
    control = "4.3"
    description = "Ensure VPC flow logging is enabled in all VPCs"
    scored = True
    for n in regions:
        client = boto3.client('ec2', region_name=n)
        flowlogs = client.describe_flow_logs(#  No paginator support in boto atm.
        )
        activeLogs = []
        for m in flowlogs['FlowLogs']:
            if "vpc-" in str(m['ResourceId']):
                activeLogs.append(m['ResourceId'])
        vpcs = client.describe_vpcs(Filters=[
                {
                    'Name': 'state',
                    'Values': [
                        'available',
                    ]
                },
            ]
        )
        for m in vpcs['Vpcs']:
            if not str(m['VpcId']) in str(activeLogs):
                result = False
                failReason = "VPC without active VPC Flow Logs found"
                offenders.append(str(n) + " : " + str(m['VpcId']))
                offenders_links.append('https://console.aws.amazon.com/vpc/home?region={region}#vpcs:filter={vpc}'.format(
                    region=n,
                    vpc=m['VpcId']
                ))
    return {'Result': result, 'failReason': failReason, 'Offenders': offenders, 'OffendersLinks': offenders_links, 'ScoredControl': scored, 'Description': description, 'ControlId': control}


# 4.4 Ensure the default security group of every VPC restricts all traffic (Scored)
def control_4_4_ensure_default_security_groups_restricts_traffic(regions):
    """Summary

    Returns:
        TYPE: Description
    """
    result = True
    failReason = ""
    offenders = []
    offenders_links = []
    control = "4.4"
    description = "Ensure the default security group of every VPC restricts all traffic"
    scored = True
    for n in regions:
        client = boto3.client('ec2', region_name=n)
        response = client.describe_security_groups(Filters=[
                {
                    'Name': 'group-name',
                    'Values': [
                        'default',
                    ]
                },
            ]
        )
        for m in response['SecurityGroups']:
            if not (len(m['IpPermissions']) + len(m['IpPermissionsEgress'])) == 0:
                result = False
                failReason = "Default security groups with ingress or egress rules discovered"
                offenders.append(str(n) + " : " + str(m['GroupId']))
                offenders_links.append('https://console.aws.amazon.com/vpc/home?region={region}#securityGroups:filter={vpc}'.format(
                    region=n,
                    vpc=m['GroupId']
                ))
    return {'Result': result, 'failReason': failReason, 'Offenders': offenders, 'OffendersLinks': offenders_links, 'ScoredControl': scored, 'Description': description, 'ControlId': control}


# 4.5 Ensure routing tables for VPC peering are "least access" (Not Scored)
def control_4_5_ensure_route_tables_are_least_access(regions):
    """Summary

    Returns:
        TYPE: Description
    """
    result = True
    failReason = ""
    offenders = []
    offenders_links = []
    control = "4.5"
    description = "Ensure routing tables for VPC peering are least access"
    scored = False
    for n in regions:
        client = boto3.client('ec2', region_name=n)
        response = client.describe_route_tables()
        for m in response['RouteTables']:
            for o in m['Routes']:
                try:
                    if o['VpcPeeringConnectionId']:
                        if int(str(o['DestinationCidrBlock']).split("/", 1)[1]) < 24:
                            result = False
                            failReason = "Large CIDR block routed to peer discovered, please investigate"
                            offenders.append(str(n) + " : " + str(m['RouteTableId']))
                            offenders_links.append('https://console.aws.amazon.com/vpc/home?region={region}#routetables:filter={route_table}'.format(
                                region=n,
                                route_table=m['RouteTableId']
                            ))
                except:
                    pass
    return {'Result': result, 'failReason': failReason, 'Offenders': offenders, 'OffendersLinks': offenders_links, 'ScoredControl': scored, 'Description': description, 'ControlId': control}


def custom_control1_ensure_guardduty_is_enabled(regions, events_rules):
    """Summary

    Returns:
        TYPE: Description
    """
    result = True
    failReason = ""
    offenders = []
    offenders_links = []
    control = "5.1"
    description = "Ensure GuardDuty is enabled in all regions and is monitored"
    failReason = "GuardDuty is not enabled in each region with an enabled CloudWatch Rule"
    scored = False
    for n in regions:
        client = boto3.client('guardduty', region_name=n)
        response = client.list_detectors()

        if not response['DetectorIds']:
            offenders.append(str(n) + " : Not enabled")
            offenders_links.append('https://console.aws.amazon.com/guardduty/home?region={region}'.format(region=n))
            result = False

        else:
            for m in response['DetectorIds']:
                response = client.get_detector(DetectorId=m)

                if response['Status'] != 'ENABLED':
                    result = False
                    offenders.append(str(n) + " : Suspended")
                    offenders_links.append('https://console.aws.amazon.com/guardduty/home?region={region}'.format(region=n))

                else:

                    rule_exists = False

                    # If GuardDuty is enabled, then determine whether notifictions are enabled.
                    for rule in events_rules[n]:

                        if 'EventPattern' in rule and 'detail' in json.loads(rule['EventPattern']):
                            detail = json.loads(rule['EventPattern'])['detail']

                            if 'eventSource' in detail and 'aws.guardduty' in detail['eventSource']:

                                rule_exists = True

                                if rule['State'] != 'ENABLED':

                                    result = False
                                    offenders.append(str(n) + " : Disabled rule")
                                    offenders_links.append('https://console.aws.amazon.com/cloudwatch/home?region={region}#rules:name={rule_name}'.format(
                                        region=n,
                                        rule_name=rule['Name'])
                                    )

                    if not rule_exists:
                        result = False
                        offenders.append(str(n) + " : No GuardDuty CloudWatch Rules exist for for this region")
                        offenders_links.append('https://console.aws.amazon.com/cloudwatch/home?region={region}'.format(region=n))

    return {'Result': result, 'failReason': failReason, 'Offenders': offenders, 'OffendersLinks': offenders_links, 'ScoredControl': scored, 'Description': description, 'ControlId': control}


def custom_control1_ensure_inspector_is_enabled(regions):
    """Summary

    Returns:
        TYPE: Description
    """
    result = True
    failReason = ""
    offenders = []
    offenders_links = []
    control = "5.2"
    description = "Ensure Inspector is enabled"
    scored = False
    client = boto3.client('inspector')
    response = client.list_assessment_targets()

    if not response['assessmentTargetArns']:
        offenders.append("Not enabled")
        offenders_links.append('https://console.aws.amazon.com/inspector/home')
        result = False
        failReason = "Inspector is not enabled"

    return {'Result': result, 'failReason': failReason, 'Offenders': offenders, 'OffendersLinks': offenders_links, 'ScoredControl': scored, 'Description': description, 'ControlId': control}

def custom_control1_ensure_macie_is_enabled(regions):
    """Summary

    Returns:
        TYPE: Description
    """
    result = False
    failReason = ""
    offenders = []
    offenders_links = []
    control = "5.3"
    description = "Ensure Macie is enabled"
    scored = False
    client = IAM_CLIENT

    try:
        # First, test for failure.
        response = client.get_role(RoleName="AWSMacieServiceCustomerSetupRole")

        # An exception wasn't thrown, so continue...
        response = boto3.client('events').list_rules()['Rules']

        for m in response:

            if 'EventPattern' in m and "aws.macie" in m['EventPattern']:
                result = True
                pass

        if not result:
            offenders.append("Account")
            offenders_links.append('https://console.aws.amazon.com/cloudwatch/home')
            result = False
            failReason = "There are no CloudWatch event rules for Macie activities"

    except:
        offenders.append("Account")
        offenders_links.append('https://console.aws.amazon.com/console/home')
        result = False
        failReason = "Macie is not enabled"

    return {'Result': result, 'failReason': failReason, 'Offenders': offenders, 'OffendersLinks': offenders_links, 'ScoredControl': scored, 'Description': description, 'ControlId': control}


# --- Central functions ---

def get_cred_report():
    """Summary

    Returns:
        TYPE: Description
    """
    x = 0
    status = ""
    while IAM_CLIENT.generate_credential_report()['State'] != "COMPLETE":
        time.sleep(2)
        x += 1
        # If no credentail report is delivered within this time fail the check.
        if x > 10:
            status = "Fail: rootUse - no CredentialReport available."
            break
    if "Fail" in status:
        return status
    response = IAM_CLIENT.get_credential_report()
    report = []
    reader = csv.DictReader(response['Content'].splitlines(), delimiter=',')
    for row in reader:
        report.append(row)

    # Verify if root key's never been used, if so add N/A
    try:
        if report[0]['access_key_1_last_used_date']:
            pass
    except:
        report[0]['access_key_1_last_used_date'] = "N/A"
    try:
        if report[0]['access_key_2_last_used_date']:
            pass
    except:
        report[0]['access_key_2_last_used_date'] = "N/A"
    return report


def get_account_password_policy():
    """Check if a IAM password policy exists, if not return false

    Returns:
        Account IAM password policy or False
    """
    try:
        response = IAM_CLIENT.get_account_password_policy()
        return response['PasswordPolicy']
    except Exception as e:
        if "cannot be found" in str(e):
            return False


def get_regions():
    """Summary

    Returns:
        TYPE: Description
    """
    client = boto3.client('ec2')
    region_response = client.describe_regions()
    regions = [region['RegionName'] for region in region_response['Regions']]
    if 'ap-northeast-3' in regions:
        regions.remove('ap-northeast-3')
    return regions


def get_cloudtrails(regions):
    """Summary

    Returns:
        TYPE: Description
    """
    trails = dict()
    for n in regions:
        client = boto3.client('cloudtrail', region_name=n)
        response = client.describe_trails()
        temp = []
        for m in response['trailList']:
            if m['IsMultiRegionTrail'] is True:
                if m['HomeRegion'] == n:
                    temp.append(m)
            else:
                temp.append(m)
        if len(temp) > 0:
            trails[n] = temp
    return trails

def get_events_rules(regions):
    """Summary

    Returns:
        TYPE: Description
    """
    events_rules = dict()
    for n in regions:
        # response = boto3.client('events').list_rules()['Rules']
        client = boto3.client('events', region_name=n)
        response = client.list_rules()
        temp = []
        for m in response['Rules']:
            temp.append(m)
        if len(temp) > 0:
            events_rules[n] = temp
    return events_rules


def find_in_string(pattern, target):
    """Summary

    Returns:
        TYPE: Description
    """
    result = True
    for n in pattern:
        if not re.search(n, target):
            result = False
            break
    return result


def get_account_number():
    """Summary

    Returns:
        TYPE: Description
    """
    if S3_WEB_REPORT_OBFUSCATE_ACCOUNT is False:
        client = boto3.client("sts")
        account = client.get_caller_identity()["Account"]
    else:
        account = "111111111111"
    return account


def set_evaluation(invokeEvent, mainEvent, annotation):
    """Summary

    Args:
        event (TYPE): Description
        annotation (TYPE): Description

    Returns:
        TYPE: Description
    """
    configClient = boto3.client('config')
    if len(annotation) > 0:
        configClient.put_evaluations(Evaluations=[
                {
                    'ComplianceResourceType': 'AWS::::Account',
                    'ComplianceResourceId': mainEvent['accountId'],
                    'ComplianceType': 'NON_COMPLIANT',
                    'Annotation': str(annotation),
                    'OrderingTimestamp': invokeEvent['notificationCreationTime']
                },
            ],
            ResultToken=mainEvent['resultToken']
        )
    else:
        configClient.put_evaluations(Evaluations=[
                {
                    'ComplianceResourceType': 'AWS::::Account',
                    'ComplianceResourceId': mainEvent['accountId'],
                    'ComplianceType': 'COMPLIANT',
                    'OrderingTimestamp': invokeEvent['notificationCreationTime']
                },
            ],
            ResultToken=mainEvent['resultToken']
        )


def json2html(controlResult, account):
    """Summary

    Args:
        controlResult (TYPE): Description

    Returns:
        TYPE: Description
    """

    print("Generating HTML report...")

    table = []
    page = []
    shortReport = shortAnnotation(controlResult)

    css = "body,html{margin:0;padding:0}a[href^=http]:after{content:" " url(data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAoAAAAKCAMAAAC67D+PAAAAB3RJTUUH4gMLDjMU49ZxvwAAAAlwSFlzAAAK8AAACvABQqw0mAAAAAlQTFRF////Xl5eVVVVUp4p0AAAAAF0Uk5TAEDm2GYAAAAnSURBVHjaY2AAAyYgABMQwMCEJAphMUCZUCEIkwHGBKvBxWSEGwsAFm4AYAhPD0EAAAAASUVORK5CYII=)}.top_bar{height:40px;background-color:#232F3F;color:#fff;text-align:center;line-height:40px;margin-bottom:20px}.attribute-table,.control-container,.control-table{font-family:Arial,Helvetica,sans-serif;border-collapse:collapse}.attribute-cell,.control-cell{box-sizing:border-box;vertical-align:middle}.active,.collapsible:hover{background-color:#555}.collapsible:after{content:'\\002B';color:#fff;font-weight:700;float:right;margin-left:5px}.active:after{content:'\\2212'}.attribute-table{display:table;width:800px;font-size:.8em;margin:0 auto;margin-bottom:20px;}.attribute-column-label{width:150px;background-color:#d3d3d3;display:table-column}.attribute-column-value{width:auto}.attribute-row{display:table-row;height:25px}.attribute-cell{display:table-cell;border:1px solid #666;padding-left:10px}.attribute-label{background-color:#ccc;width:150px;font-weight:700}.attribute-value{width:auto}.collapsible{background-color:#777;color:#fff;cursor:pointer;padding:12px;width:100%;border:none;text-align:left;outline:0;font-size:14px;height:40px;margin-bottom:10px}.content{max-height:0;overflow:hidden;transition:max-height .2s ease-out}.control-container{display:block;width:800px;font-size:.8em;margin:30px auto 0}.control-table{display:table;width:798px;margin:0 auto;margin-bottom:10px}.control-column-label{width:150px;background-color:#d3d3d3;display:table-column}.control-column-value{width:150px}.control-row{display:table-row;height:25px}.control-cell{display:table-cell;border:1px solid #666;padding-left:10px}.control-label{background-color:#ccc;font-weight:700}.control-value:hover{background-color:#d3d3d3}.result-failure{background-color:#ff6666}"

    page.append('<html><head><style>' + css + '</style></head><body>')

    report_details = '''
    <div class="top_bar">AWS Cloud Wellness Report</div>
    <div class="attribute-table">
        <div class="attribute-column-label"></div>
        <div class="attribute-column-value"></div>

        <div class="attribute-row">
            <div class="attribute-cell attribute-label">Account:</div>
            <div class="attribute-cell attribute-value">{0}</div>
        </div>
        <div class="attribute-row">
            <div class="attribute-cell attribute-label">Report date:</div>
            <div class="attribute-cell attribute-value">{1}</div>
        </div>
        <div class="attribute-row">
            <div class="attribute-cell attribute-label">JSON Results</div>
            <div class="attribute-cell attribute-value">{3}</div>
        </div>
    </div>
    '''.format(
            account,
            time.strftime("%c"),
            AWS_CLOUD_WELLNESS_STANDARD_VERSION,
            shortReport
        )

    page.append(report_details)

    page.append('    <div class="control-container">')

    for m, _ in enumerate(controlResult):
        page.append('''
            <button class="collapsible">{control_label} Controls ({control_count})</button>
            <div class="content">
            '''.format(
                    control_label=CONTROL_LABEL_MAP[str(controlResult[m][0]['ControlId'].split('.')[0])],
                    control_count=len(controlResult[m]
                )
            )
        )

        for n in range(len(controlResult[m])):

            # The section will be highlighted for failed controls.
            result_class = " result-failure" if controlResult[m][n]['Result'] == False else ""

            page.append('''
                <div class="control-table{result_status}">
                    <div class="control-column-label"></div>
                    <div class="control-column-value"></div>
                    <div class="control-row">
                        <div class="control-cell control-label">Control ID:</div>
                        <div class="control-cell control-value">{control_id}</div>
                    </div>
                    <div class="control-row">
                        <div class="control-cell control-label">Description:</div>
                        <div class="control-cell control-value">{description}</div>
                    </div>
                    <div class="control-row">
                        <div class="control-cell control-label">Result:</div>
                        <div class="control-cell control-value">{result}</div>
                    </div>
                '''.format(
                        result_status=result_class,
                        result="Pass" if controlResult[m][n]['Result'] else "Fail",
                        control_id=controlResult[m][n]['ControlId'],
                        description=controlResult[m][n]['Description']
                    )
            )

            # Only show these fields in case of failure.
            if controlResult[m][n]['Result'] == False:

                page.append('''
                        <div class="control-row">
                            <div class="control-cell control-label">Fail Reason:</div>
                            <div class="control-cell control-value">{fail_reason}</div>
                        </div>
                    '''.format(
                            fail_reason=controlResult[m][n]['failReason']
                        )
                )

                offenders_links = format_offenders(controlResult[m][n])
                # Only display offenders if there are any.
                if offenders_links:
                    page.append('''
                            <div class="control-row">
                                <div class="control-cell control-label">Offenders:</div>
                                <div class="control-cell control-value">{offenders}</div>
                            </div>
                        '''.format(
                                offenders=offenders_links
                            )
                    )

            page.append('''
                    <div class="control-row">
                        <div class="control-cell control-label">Scored Control:</div>
                        <div class="control-cell control-value">{scored_control}</div>
                    </div>
                '''.format(
                        scored_control=controlResult[m][n]['ScoredControl']
                    )
            )

            page.append('</div>')

        page.append('</div>')

    js_collapse = '''
        <script>
        var i, coll = document.getElementsByClassName("collapsible");
        for (i = 0; i < coll.length; i++) coll[i].addEventListener("click", function() {
            this.classList.toggle("active");
            var l = this.nextElementSibling;
            l.style.maxHeight ? l.style.maxHeight = null : l.style.maxHeight = l.scrollHeight + "px"
        });
        </script>
    '''

    page.append(js_collapse + '</body></html>')

    return page


def s3report(htmlReport, account):
    """Summary

    Args:
        htmlReport (TYPE): Description

    Returns:
        TYPE: Description
    """
    if S3_WEB_REPORT_NAME_DETAILS is True:
        reportName = "aws_cloud_wellness_report_" + \
            str(account) + "_" + \
            str(datetime.now().strftime('%Y%m%d_%H%M')) + ".html"
    else:
        reportName = "aws_cloud_wellness_report.html"
    with tempfile.NamedTemporaryFile(delete=False) as f:
        for item in htmlReport:
            f.write(item)
            f.flush()
        try:
            f.close()
            S3_CLIENT.upload_file(f.name,
                output_bucket,
                reportName,
                ExtraArgs={'ContentType': 'text/html'})
            os.unlink(f.name)
        except Exception as e:
            return "Failed to upload report to S3 because: " + str(e)
    ttl = int(S3_WEB_REPORT_EXPIRE) * 60
    signedURL = S3_CLIENT.generate_presigned_url('get_object',
        Params={
            'Bucket': output_bucket,
            'Key': reportName
        },
        ExpiresIn=ttl)
    return signedURL


def json_output(controlResult):
    """Summary

    Args:
        controlResult (TYPE): Description

    Returns:
        TYPE: Description
    """
    inner = dict()
    outer = dict()
    for m in range(len(controlResult)):
        inner = dict()
        for n in range(len(controlResult[m])):
            x = int(controlResult[m][n]['ControlId'].split('.')[1])
            inner[x] = controlResult[m][n]
        y = controlResult[m][0]['ControlId'].split('.')[0]
        outer[y] = inner
    if OUTPUT_ONLY_JSON is True:
        print(json.dumps(outer, sort_keys=True, indent=4, separators=(',', ': ')))
    else:
        print("JSON output:")
        print("-------------------------------------------------------")
        print(json.dumps(outer, sort_keys=True, indent=4, separators=(',', ': ')))
        print("-------------------------------------------------------")
        print("\n")
        print("Summary:")
        print(shortAnnotation(controlResult))
        print("\n")
    return 0

def format_offenders(control):
    """Summary

    Args:
        control (TYPE): Control

    Returns:
        TYPE: Formatted offender
    """
    offenders_links = ''

    # print(control)

    if control['Offenders']:
        if not 'OffendersLinks' in control:
            # Just output the offenders.
            offenders_links = str(control['Offenders'])
        else:
            # Format the offenders.
            for i in range(len(control['Offenders'])):

                link = ''
                if i < len(control['OffendersLinks']):
                    link = '&nbsp;<a href="{link}" target="_blank"></a>'.format(link=control['OffendersLinks'][i])

                offenders_links += '{offender}{link}<br/>'.format(
                    link=link,
                    offender=control['Offenders'][i]
                    )

    else:
        offenders_links = ""

    return offenders_links

def shortAnnotation(controlResult):
    """Summary

    Args:
        controlResult (TYPE): Description

    Returns:
        TYPE: Description
    """
    annotation = []
    longAnnotation = False
    for m, _ in enumerate(controlResult):
        for n in range(len(controlResult[m])):
            if controlResult[m][n]['Result'] is False:
                # if len(str(annotation)) < 220:
                #     annotation.append(controlResult[m][n]['ControlId'])
                # else:
                #     longAnnotation = True
                annotation.append(controlResult[m][n]['ControlId'])

    if longAnnotation:
        annotation.append("etc")
        return "{\"Failed\":" + json.dumps(annotation) + "}"
    else:
        return "{\"Failed\":" + json.dumps(annotation) + "}"


def send_results_to_sns(url):
    """Summary

    Args:
        url (TYPE): SignedURL created by the S3 upload function

    Returns:
        TYPE: Description
    """
    # Get correct region for the TopicARN
    region = (SNS_TOPIC_ARN.split("sns:", 1)[1]).split(":", 1)[0]
    client = boto3.client('sns', region_name=region)
    client.publish(TopicArn=SNS_TOPIC_ARN,
        Subject="AWS AWS Cloud Wellness report - " + str(time.strftime("%c")),
        Message=json.dumps({'default': url}),
        MessageStructure='json'
    )


def lambda_handler(event, context):
    """Summary

    Args:
        event (TYPE): Description
        context (TYPE): Description

    Returns:
        TYPE: Description
    """
    # Run all control validations.
    # The control object is a dictionary with the value
    # result : Boolean - True/False
    # failReason : String - Failure description
    # scored : Boolean - True/False
    # Check if the script is initiade from AWS Config Rules
    try:
        if event['configRuleId']:
            configRule = True
            # Verify correct format of event
            invokingEvent = json.loads(event['invokingEvent'])
    except:
        configRule = False


    # Globally used resources
    print("Retrieving global resources...")
    region_list = get_regions()
    cred_report = get_cred_report()
    password_policy = get_account_password_policy()
    cloud_trails = get_cloudtrails(region_list)
    events_rules = get_events_rules(region_list)
    accountNumber = get_account_number()

    # Run individual controls.
    # Comment out unwanted controls
    print("Evaluating IAM controls...")
    control1 = []
    control1.append(control_1_1_root_use(cred_report))
    control1.append(control_1_2_mfa_on_password_enabled_iam(cred_report))
    control1.append(control_1_3_unused_credentials(cred_report))
    control1.append(control_1_4_rotated_keys(cred_report))
    control1.append(control_1_5_password_policy_uppercase(password_policy))
    control1.append(control_1_6_password_policy_lowercase(password_policy))
    control1.append(control_1_7_password_policy_symbol(password_policy))
    control1.append(control_1_8_password_policy_number(password_policy))
    control1.append(control_1_9_password_policy_length(password_policy))
    control1.append(control_1_10_password_policy_reuse(password_policy))
    control1.append(control_1_11_password_policy_expire(password_policy))
    control1.append(control_1_12_root_key_exists(cred_report))
    control1.append(control_1_13_root_mfa_enabled())
    control1.append(control_1_14_root_hardware_mfa_enabled())
    control1.append(control_1_15_security_questions_registered())
    control1.append(control_1_16_no_policies_on_iam_users())
    control1.append(control_1_17_detailed_billing_enabled())
    control1.append(control_1_18_ensure_iam_master_and_manager_roles())
    control1.append(control_1_19_maintain_current_contact_details())
    control1.append(control_1_20_ensure_security_contact_details())
    control1.append(control_1_21_ensure_iam_instance_roles_used())
    control1.append(control_1_22_ensure_incident_management_roles())
    control1.append(control_1_23_no_active_initial_access_keys_with_iam_user(cred_report))
    control1.append(control_1_24_no_overly_permissive_policies())

    print("Evaluating logging controls...")
    control2 = []
    control2.append(control_2_1_ensure_cloud_trail_all_regions(cloud_trails))
    control2.append(control_2_2_ensure_cloudtrail_validation(cloud_trails))
    control2.append(control_2_3_ensure_cloudtrail_bucket_not_public(cloud_trails))
    control2.append(control_2_4_ensure_cloudtrail_cloudwatch_logs_integration(cloud_trails))
    control2.append(control_2_5_ensure_config_all_regions(region_list))
    control2.append(control_2_6_ensure_cloudtrail_bucket_logging(cloud_trails))
    control2.append(control_2_7_ensure_cloudtrail_encryption_kms(cloud_trails))
    control2.append(control_2_8_ensure_kms_cmk_rotation(region_list))

    print("Evaluating monitoring controls...")
    control3 = []
    control3.append(control_3_1_ensure_log_metric_filter_unauthorized_api_calls(cloud_trails))
    control3.append(control_3_2_ensure_log_metric_filter_console_signin_no_mfa(cloud_trails))
    control3.append(control_3_3_ensure_log_metric_filter_root_usage(cloud_trails))
    control3.append(control_3_4_ensure_log_metric_iam_policy_change(cloud_trails))
    control3.append(control_3_5_ensure_log_metric_cloudtrail_configuration_changes(cloud_trails))
    control3.append(control_3_6_ensure_log_metric_console_auth_failures(cloud_trails))
    control3.append(control_3_7_ensure_log_metric_disabling_scheduled_delete_of_kms_cmk(cloud_trails))
    control3.append(control_3_8_ensure_log_metric_s3_bucket_policy_changes(cloud_trails))
    control3.append(control_3_9_ensure_log_metric_config_configuration_changes(cloud_trails))
    control3.append(control_3_10_ensure_log_metric_security_group_changes(cloud_trails))
    control3.append(control_3_11_ensure_log_metric_nacl(cloud_trails))
    control3.append(control_3_12_ensure_log_metric_changes_to_network_gateways(cloud_trails))
    control3.append(control_3_13_ensure_log_metric_changes_to_route_tables(cloud_trails))
    control3.append(control_3_14_ensure_log_metric_changes_to_vpc(cloud_trails))
    control3.append(control_3_15_verify_sns_subscribers())
    control3.append(control_3_16_ensure_log_metric_changes_to_organizations(cloud_trails))

    print("Evaluating network controls...")
    control4 = []
    control4.append(control_4_1_ensure_ssh_not_open_to_world(region_list))
    control4.append(control_4_2_ensure_rdp_not_open_to_world(region_list))
    control4.append(control_4_3_ensure_flow_logs_enabled_on_all_vpc(region_list))
    control4.append(control_4_4_ensure_default_security_groups_restricts_traffic(region_list))
    control4.append(control_4_5_ensure_route_tables_are_least_access(region_list))

    print("Evaluating custom controls...")
    control_custom = []
    control_custom.append(custom_control1_ensure_guardduty_is_enabled(region_list, events_rules))
    control_custom.append(custom_control1_ensure_inspector_is_enabled(region_list))
    control_custom.append(custom_control1_ensure_macie_is_enabled(region_list))

    # Join results
    controls = []
    controls.append(control1)
    controls.append(control2)
    controls.append(control3)
    controls.append(control4)
    controls.append(control_custom)

    # Build JSON structure for console output if enabled
    if SCRIPT_OUTPUT_JSON:
        json_output(controls)

    # Create HTML report file if enabled
    if S3_WEB_REPORT:
        htmlReport = json2html(controls, accountNumber)
        if S3_WEB_REPORT_OBFUSCATE_ACCOUNT:
            for n, _ in enumerate(htmlReport):
                htmlReport[n] = re.sub(r"\d{12}", "xxxxxxxxxxxx", htmlReport[n])
        signedURL = s3report(htmlReport, accountNumber)
        if OUTPUT_ONLY_JSON is False:
            print("SignedURL:\n" + signedURL)
        if SEND_REPORT_URL_TO_SNS is True:
            send_results_to_sns(signedURL)

    # Report back to Config if we detected that the script is initiated from Config Rules
    if configRule:
        evalAnnotation = shortAnnotation(controls)
        set_evaluation(invokingEvent, event, evalAnnotation)


if __name__ == '__main__':
    profile_name = ''
    try:
        opts, args = getopt.getopt(sys.argv[1:], "p:h:ob:pr", [
                                   "profile=", "help", "output-bucket="])

    except getopt.GetoptError:
        print("Error: Illegal option\n")
        print("---Usage---")
        print('Run without parameters to use default profile:')
        print("python " + sys.argv[0] + "\n")
        print("Use -p or --profile to specify a specific profile:")
        print("python " + sys.argv[0] + ' -p <profile>')
        sys.exit(2)

    output_bucket = S3_WEB_REPORT_BUCKET #default

    # Parameter options
    for opt, arg in opts:
        if opt in ("-h", "--help"):
            print("---Help---")
            print('Run without parameters to use default profile:')
            print("python " + sys.argv[0] + "\n")
            print("     -p, --profile <profile>")
            print("         specify a specific profile\n")
            print("     -ob, --output-bucket <bucket-name>")
            print("         specify an S3 bucket to store the HTML report")
            sys.exit()
        elif opt in ("-p", "--profile"):
            profile_name = arg
        elif opt in ("-ob", "--output-bucket"):
            output_bucket = arg

    print("")

    # Verify that the profile exists
    if not profile_name == "":
        try:
            print("Using profile: {}".format(profile_name))

            boto3.setup_default_session(profile_name=profile_name)
            # Update globals with new profile
            IAM_CLIENT = boto3.client('iam')
            S3_CLIENT = boto3.client('s3')
        except Exception as e:
            if "could not be found" in str(e):
                print("Error: " + str(e))
                print("Please verify your profile name.")
                sys.exit(2)
    else:
        print("Using default profile")

    # Test if default region is configured for the used profile, if not we will use us-east-1
    try:
        client = boto3.client('ec2')
    except Exception as e:
        if "You must specify a region" in str(e):
            if profile_name == "":
                boto3.setup_default_session(region_name='us-east-1')
            else:
                boto3.setup_default_session(profile_name=profile_name, region_name='us-east-1')

    lambda_handler("", "")
