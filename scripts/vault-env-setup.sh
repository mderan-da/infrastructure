#!/bin/bash

MAX_TTL='12h'
RENEWAL_PERIOD='60m'

after_script(){
  echo "name: $AWS_ACCOUNT_NAME"
  # build the Vault server address based on the AWS account name
  if [ ! $AWS_ACCOUNT_NAME = "" ]; then
    echo "Using AWS account: $AWS_ACCOUNT_NAME"
  else
    echo "Could not find AWS account name. Make sure AWS_ACCOUNT_NAME is set."
    return
  fi

  export VAULT_ADDR="https://vault.$AWS_ACCOUNT_NAME.umccr.org:8200"

  # check if there is already a Vault token
  if [ ! $VAULT_TOKEN = "" ]; then
    echo "Found a Vault token, trying to renew it..."
    vault token renew $VAULT_TOKEN
    if [ $? == 0 ]; then
      echo "Renewal successful." # or at least no error, so we stop here
      return
    fi
    # if the renewal was unsuccessful, we attempt to create a new one
    echo "Renewal unsuccessful. Trying to request a new token..."
  fi

  # if GITHUB_TOKEN is not set, the vault login will ask for it
  echo "Attempting a Vault login"
  vault login -method=github token=$GITHUB_TOKEN
  if [ $? != 0 ]; then
    echo "ERROR logging into Vault."
    return
  fi

  echo "Requesting Vault access token..."
  vault_token=$(vault token create -explicit-max-ttl=$MAX_TTL -period=$RENEWAL_PERIOD --format=json | jq -r .auth.client_token)
  if [ $vault_token = "" ]; then
    echo "ERROR requesting Vault token"
  fi
  export VAULT_TOKEN="$vault_token"
  echo "Vault access token successful retrieved. Session envars exported."
}

after_script;
