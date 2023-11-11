import json
import boto3
import botocore
import os

AWS_REGION = os.environ['AWS_DEFAULT_REGION']
PERMISSION_TABLE = os.environ['PERMISSION_TABLE_NAME']
USER_TABLE = os.environ['USER_TABLE_NAME']

ddb = boto3.resource('dynamodb')
iam = boto3.client('iam')

def handler(event, context):
    # Log the event argument for debugging and for use in local development.
    print(json.dumps(event))
    IDENTITY_STORE_ID = event['identityStoreId']
    INSTANCE_ARN = event['instanceArn']
    SSO_DEPLOYED_REGION = event['ssoDeployedRegion']
    
    sso = boto3.client('sso-admin', region_name=SSO_DEPLOYED_REGION)
    identitystore = boto3.client('identitystore', region_name=SSO_DEPLOYED_REGION)

    iam_permissions_table = ddb.Table(PERMISSION_TABLE)
    user_list_table = ddb.Table(USER_TABLE)

    permission_sets_response = sso.list_permission_sets(
        InstanceArn=INSTANCE_ARN
    )
    print(permission_sets_response)
    
    permission_sets_list = permission_sets_response.get('PermissionSets')
    
    # if total number of permission sets exceed the size of one page
    while 'NextToken' in permission_sets_response:
        permission_sets_response = sso.list_permission_sets(
            InstanceArn=INSTANCE_ARN,
            NextToken=permission_sets_response['NextToken']
            )
        # Store paginated results into a list
        for permission_set in permission_sets_response.get('PermissionSets'):
            permission_sets_list.append(permission_set)
    
    print("number of permission sets to be analysed:", len(permission_sets_list))
    # loop through permission set to get list of group ID (to get sso group and users), policies, default versions (for json details)
    for permission_set_arn in permission_sets_response.get('PermissionSets'):
        print("currently analysing PermissionSet:", permission_set_arn)
        # get list of accounts associated with permission set
        assoc_acc_response = sso.list_accounts_for_provisioned_permission_set(
            InstanceArn=INSTANCE_ARN,
            PermissionSetArn=permission_set_arn
            )
            
        assoc_acc_list = assoc_acc_response.get('AccountIds')
        
        # if total number of associated accounts exceed the size of one page
        while 'NextToken' in assoc_acc_response:
            assoc_acc_response = sso.list_accounts_for_provisioned_permission_set(
                InstanceArn=INSTANCE_ARN,
                PermissionSetArn=permission_set_arn,
                NextToken=assoc_acc_response['NextToken']
                )
            # Store paginated results into a list
            for account in assoc_acc_response.get('AccountIds'):
                assoc_acc_list.append(account)
        
        describe_permission_set_response = sso.describe_permission_set(
            InstanceArn=INSTANCE_ARN,
            PermissionSetArn=permission_set_arn
            )

        # Define array to store Principal ID, Account ID and Principal Type
        principal_id_list=[]
        account_list=[]
        principal_type_list=[]
        
        for account in assoc_acc_list:
            # get the principal ID to link with the AWS IAM Identity Center group to get members
            account_assignments_response = sso.list_account_assignments(
                InstanceArn=INSTANCE_ARN,
                AccountId=account,
                PermissionSetArn=permission_set_arn
                )
                
            identity_principal_assignee_list = account_assignments_response.get('AccountAssignments')
                
            # if total number of principal assigned exceed the size of one page
            while 'NextToken' in account_assignments_response:
                account_assignments_response = sso.list_account_assignments(
                    InstanceArn=INSTANCE_ARN,
                    AccountId=account,
                    PermissionSetArn=permission_set_arn,
                    NextToken=account_assignments_response['NextToken']
                    )
                # Store paginated results into a list
                for principal_assignee in account_assignments_response.get('AccountAssignments'):
                    identity_principal_assignee_list.append(account)

            for principal_assignee in identity_principal_assignee_list:
                # build json
                principal_id_list.append(principal_assignee['PrincipalId'])
                account_list.append(principal_assignee['AccountId'])
                principal_type_list.append(principal_assignee['PrincipalType'])
                
        # get the list of managed policies attached in each of the permission set
        managed_policies_response = sso.list_managed_policies_in_permission_set(
            InstanceArn=INSTANCE_ARN,
            PermissionSetArn=permission_set_arn
            )
                
        managed_policies = []
        # loop through each policy arn to get version ID in order to list out json
        for i in managed_policies_response['AttachedManagedPolicies']:
            policy_details = iam.get_policy(
                PolicyArn=i['Arn']
                )
            defaultVersionId = policy_details['Policy']['DefaultVersionId']
            policy_json = iam.get_policy_version(
                PolicyArn=i['Arn'],
                VersionId=defaultVersionId
                )
            managed_policies.append({'policryArn': i['Arn'], 'policy_type': 'aws_managed', 'policyJson': json.dumps(policy_json['PolicyVersion']['Document'], default=str)})
        
        # get the inline policy attached in each of the permission set
        inline_policies_response = sso.get_inline_policy_for_permission_set(
            InstanceArn=INSTANCE_ARN,
            PermissionSetArn=permission_set_arn
            )

        # get the list of customer managed policy references attached in each of the permission set
        customer_policies_response = sso.list_customer_managed_policy_references_in_permission_set(
            InstanceArn=INSTANCE_ARN,
            PermissionSetArn=permission_set_arn
            )
        # print(customer_policies_response)

        # get the permission boundary attached in each of the permission set
        try: 
            permissions_boundary_response = sso.get_permissions_boundary_for_permission_set(
                InstanceArn=INSTANCE_ARN,
                PermissionSetArn=permission_set_arn
                )
        except botocore.exceptions.ClientError as error:
            if error.response['Error']['Code'] == 'ResourceNotFoundException':
                print('No Permission Boundary attached')
                permissions_boundary_response = {'PermissionsBoundary':''}
            else:
                raise error

        # store all in ddb table
        iam_permissions_table.put_item(
            TableName=PERMISSION_TABLE,
            Item={
                'id': INSTANCE_ARN,
                'permissionSetArn': permission_set_arn,
                'permissionSetName': describe_permission_set_response['PermissionSet']['Name'],
                'principalId': principal_id_list,
                'accountId': account_list,
                'principalType': principal_type_list,
                'managedPolicies': managed_policies,
                'inlinePolicies': inline_policies_response['InlinePolicy'],
                'customerPolicies': customer_policies_response['CustomerManagedPolicyReferences'],
                'permissionsBoundary': permissions_boundary_response['PermissionsBoundary']
            })

    # Get list of users in Identity Store
    user_list_response = identitystore.list_users(
        IdentityStoreId=IDENTITY_STORE_ID
    )
    
    user_list = user_list_response.get('Users')
    
    # if total number of users exceed the size of one page
    while 'NextToken' in user_list_response:
        user_list_response = identitystore.list_users(
        IdentityStoreId=IDENTITY_STORE_ID,
        NextToken=user_list_response['NextToken']
        )
        # Store paginated results into a list
        for user in user_list_response.get('Users'):
            user_list.append(user)

    
    # loop through user lists to get the membership details (user Id, group ID in GroupMemberships details, user names etc)
    for user in user_list:
        # get member group (groupId) details
        user_group_membership_response = identitystore.list_group_memberships_for_member(
            IdentityStoreId=IDENTITY_STORE_ID,
            MemberId={
                'UserId': user['UserId']
            }
        )
        group_name_list=[]
        for group in user_group_membership_response['GroupMemberships']:
            group_description_response = identitystore.describe_group(
                IdentityStoreId=IDENTITY_STORE_ID,
                GroupId=group['GroupId']
                )
            group_name_list.append(group_description_response['DisplayName'])
        
        # store all in ddb table
        user_list_table.put_item(
            TableName=USER_TABLE,
            Item={
                'userId': user['UserId'],
                'userName': user['UserName'],
                'groupMemberships': user_group_membership_response['GroupMemberships'],
                'groupName': group_name_list
            })
    
    return event