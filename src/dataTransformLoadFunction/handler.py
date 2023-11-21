import json
import boto3
import os
from datetime import date
import csv

PERMISSION_TABLE = os.environ['PERMISSION_TABLE_NAME']
USER_TABLE = os.environ['USER_TABLE_NAME']
SNS_ARN = os.environ['TOPIC_ARN']
BUCKET_NAME = os.environ['BUCKET_NAME']

ddb = boto3.resource('dynamodb')
iam = boto3.client('iam')
sns = boto3.client('sns')
s3 = boto3.client('s3')

def query_ddb_to_populate_report(user_name, principal_id, group_name, principal_type, iam_permissions_table, instance_arn, writer):
    permission_response = iam_permissions_table.query(
        TableName=PERMISSION_TABLE,
        KeyConditionExpression="id = :id",
        FilterExpression= "contains(principalId, :pid)",
        ExpressionAttributeValues={
            ':id': instance_arn,
            ':pid': principal_id
        }
        )
    permission_response_list = permission_response.get('Items')
    
    # Paginate returning up to 1MB of data for each iteration
    while 'LastEvaluatedKey' in permission_response:
        permission_response = iam_permissions_table.query(
        TableName=PERMISSION_TABLE,
        KeyConditionExpression="id = :id",
        FilterExpression= "contains(principalId, :pid)",
        ExpressionAttributeValues={
            ':id': instance_arn,
            ':pid': principal_id
        },
        ExclusiveStartKey=permission_response['LastEvaluatedKey']
        )
        # Extend paginated results into the list
        permission_response_list.extend(permission_response.get('Items'))

    print('Dynamodb query result for user:' + user_name + ', group name:'+ group_name)
    print(permission_response_list)

    if len(permission_response_list) == 0:
        writer.writerow([user_name, principal_id, principal_type, group_name, 'not_assigned'])
    else:
        for permission in permission_response_list:
            print('Permissions for user:' + user_name + ', group name:'+ group_name)
            print(permission)
            
            # Excel has a 32,767 char limit, check if each policy exceeds the limit
            policy_type_list = ['inlinePolicies', 'customerPolicies','managedPolicies' ]
            for policy_type in policy_type_list:
                # return policy arn for AWS managed policies
                managed_policy_arn_list = []
                if policy_type == 'managedPolicies':
                    for policy in permission[policy_type]:
                        managed_policy_arn_list.append(policy['policryArn'])
                    permission[policy_type] = managed_policy_arn_list
                            
                if len(str(permission[policy_type])) > 32700:
                    permission[policy_type] = 'Exceed character limit for excel, refer to AWS Console for full policy details'
                
            # Loop through all assignments of a permission set for individual users and groups
            for no_of_assignments, accountid in enumerate(permission['accountId']):
                # Additional principal id check to prevent duplicated records (a user can be assigned individually or assigned as part of a group) and incorrect records (permission set filter will have other principals included)
                if principal_id == permission['principalId'][no_of_assignments]:
                    writer.writerow([user_name, principal_id, permission['principalType'][no_of_assignments], group_name, accountid, permission['permissionSetArn'], permission['permissionSetName'], permission['inlinePolicies'], permission['customerPolicies'], permission['managedPolicies'], permission['permissionsBoundary']])
                    
            
                    
def handler(event, context):
    # Log the event argument for debugging and for use in local development.
    print(json.dumps(event))
    payload = event['Payload']
    INSTANCE_ARN = payload['instanceArn']
    today = date.today()
    curr_date = today.strftime("%m%d%y")
    S3_UPLOAD_KEY = curr_date + 'result.csv'
    
    iam_permissions_table = ddb.Table(PERMISSION_TABLE)
    user_list_table = ddb.Table(USER_TABLE)
    
    user_list_response = user_list_table.scan(
        TableName=USER_TABLE
        )
    user_list_data = user_list_response.get('Items')
    
    # Paginate returning up to 1MB of data for each iteration
    while 'LastEvaluatedKey' in user_list_response:
        user_list_response = user_list_table.scan(
            TableName=USER_TABLE,
            ExclusiveStartKey=user_list_response['LastEvaluatedKey']
            )
        # Extend paginated results into the list
        user_list_data.extend(user_list_response.get('Items'))

    with open('/tmp/' + curr_date + 'result.csv', 'w') as f:
        writer = csv.writer(f)
        writer.writerow(['User', 'PrincipalId', 'PrincipalType', 'GroupName', 'AccountIdAssignment', 'PermissionSetARN', 'PermissionSetName', 'Inline Policy', 'Customer Managed Policy','AWS Managed Policy', 'Permission Boundary'])
        
        for user in user_list_data:
            print('extracting user data')
            print(user)
            user_id = user['userId']
            user_name = user['userName']
            group_name = ''
       
            # Check individual user assignment first
            query_ddb_to_populate_report(user_name, user_id, group_name, 'USER', iam_permissions_table, INSTANCE_ARN, writer)

            # Check if user is in a group and group assignment 
            if user['groupMemberships']:
                for idx, group in enumerate(user['groupMemberships']):
                    group_id = group['GroupId']
                    group_name = user['groupName'][idx]
                    print('groupname is: ' + group_name)
                    query_ddb_to_populate_report(user_name, group_id, group_name, 'GROUP', iam_permissions_table, INSTANCE_ARN, writer)
   
    s3.upload_file('/tmp/' + S3_UPLOAD_KEY, BUCKET_NAME, S3_UPLOAD_KEY)
    
    sns_message = "Analysis of users list with granted permission policies have been completed. \n Find out more from the report stored in the S3 bucket " + BUCKET_NAME + ", with the object key name: " + S3_UPLOAD_KEY
    sns.publish(
        TopicArn = SNS_ARN,
        Message = sns_message,
        Subject='AWS IAM Identity Center Policies Analyzer Report'
        )
        
    return {}