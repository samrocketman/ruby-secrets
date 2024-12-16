#!/bin/bash
# Created by Sam Gleske (https://github.com/samrocketman/repository-secrets)
# Copyright (c) 2015-2024 Sam Gleske - https://github.com/samrocketman/repository-secrets
# MIT Licensed
# Fri Dec 13 21:29:09 EST 2024
# Pop!_OS 22.04 LTS
# Linux 6.9.3-76060903-generic x86_64
# GNU bash, version 5.1.16(1)-release (x86_64-pc-linux-gnu)
# yq (https://github.com/mikefarah/yq/) version v4.44.2
# tr (GNU coreutils) 8.32
# OpenSSL 3.0.2 15 Mar 2022 (Library: OpenSSL 3.0.2 15 Mar 2022)
# DESCRIPTION
#   A script for encrypting and decrypting secret data.  The purpose of this
#   script is to provide developers a way to asymmetrically encrypt data on
#   client with an RSA public key.  A backend server will use this same script
#   to decrypt the data with an RSA private key.
# REQUIREMENTS
#   Some coreutils (tr, shasum or sha256sum)
#   yq 4.x

set -euo pipefail

#
# ENVIRONMENT AND DEFAULTS
#
openssl_saltlen="${openssl_saltlen:-16}"
openssl_aes_args="${openssl_aes_args:--aes-256-cbc -pbkdf2 -iter 600000 -saltlen ${openssl_saltlen}}"
openssl_rsa_args="${openssl_rsa_args:--pkeyopt rsa_padding_mode:oaep -pkeyopt rsa_oaep_md:SHA256}"
PRIVATE_KEY="${PRIVATE_KEY:-/tmp/id_rsa}"
PUBLIC_KEY="${PUBLIC_KEY:-/tmp/id_rsa.pub}"

#
# PREREQUISITE UTILITIES
#
missing_util() {
  for x in "$@"; do
    if type -P "$x" &> /dev/null; then
      return 0
    fi
  done
  echo 'Missing utility: '"$@"
  return 1
}
needs_util=0
missing_util shasum sha256sum || needs_util=1
missing_util tr || needs_util=1
missing_util yq || needs_util=1
missing_util bash || needs_util=1
missing_util openssl || needs_util=1
missing_util mktemp || needs_util=1
missing_util cat || needs_util=1
missing_util cp || needs_util=1
if [ "${needs_util}" = 1 ]; then
  exit 1
fi

#
# PRE-RUN SETUP
#
TMP_DIR="$(mktemp -d)"
trap 'rm -rf "${TMP_DIR}"' EXIT
export TMP_DIR
chmod 700 "${TMP_DIR}"
output_file='-'
input_file='-'
sub_command=''
skip_fields=()

#
# FUNCTIONS
#
helptext() {
cat <<EOF
SYNOPSIS
  $0 [sub_command] [options]


DESCRIPTION
  A utility for performing one-way encryption or decryption on files using RSA
  key pairs.  The intent is to have a client encrypt data with an RSA public
  key and a backend system use this same script to decrypt the data with an RSA
  private key.


SUBCOMMANDS
  encrypt
      Performs encryption operations with an RSA public key and outputs an
      encrypted cipher YAML file.  Binary data is allowed.

  decrypt
      Performs decryption operations with an RSA private key and outputs
      original plain text.  May output binary data if binary data was
      originally encrypted.

  rotate-key
      Performs private key rotation on enciphered YAML without changing
      symmetrically encrypted data.  This will not modify data,
      openssl_aes_args, or openssl_rsa_args keys in the enciphered YAML.


ENCRYPT SUBCOMMAND OPTIONS
  -p FILE
  --public-key FILE
    An RSA public key which will be used for encrypting data.
    Default: PUBLIC_KEY environment variable

  -i FILE
  --in-file FILE
    Plain input meant to be encrypted.  Can be plain text or binary data.
    Default: stdin

  -o FILE
  --output FILE
    Encrypted ciphertext in a plain-text friendly YAML format.  If the output
    file already exists as cipher YAML, then only the data and hash will be
    updated.
    Default: stdout


DECRYPT SUBCOMMAND OPTIONS
  -k FILE
  --private-key FILE
    An RSA private key which will be used for decrypting data.
    Default: PRIVATE_KEY environment variable

  -i FILE
  --in-file FILE
    Encrypted ciphertext in a plain-text friendly YAML format.
    Default: stdin

  -o FILE
  --output FILE
    Plain input meant to be which has been decrypted.
    Default: stdout

  -s FIELD
  --skip-field FIELD
    Sometimes upon decryption you may want to override the AES or RSA
    decryption options.  This option allows you to set an environment variable
    of the same name while ignoring the value in the cipher YAML file.  FIELD
    may be one of the following values: openssl_aes_args or openssl_rsa_args.
    This option can be specified multiple times to skip multiple fields.
    Default: ''


ROTATE-KEY SUBCOMMAND OPTIONS
  -k FILE
  --private-key FILE
    An RSA private key which will be used to decrypt keys salt, passin, and
    hash within a cipher YAML file.
    Default: PRIVATE_KEY environment variable

  -p FILE
  --public-key FILE
    An RSA public key which will be used to re-encrypt keys salt, passin, and
    hash within a cipher YAML file.
    Default: PUBLIC_KEY environment variable

  -f FILE
  --input-output-file FILE
    A cipher YAML file in which the salt, passin, and hash are updated with the
    new private key.  The data will not be modified.


ENVIRONMENT VARIABLES
  openssl_saltlen
    The length of salt used by PBKDF2 during encryption or decryption.  Must be
    an integer between 1 and 16.
    Default: '${openssl_saltlen:-}'

  openssl_aes_args
    Arguments used on openssl for AES encryption or decryption.
    Default: '${openssl_aes_args:-}'

  openssl_rsa_args
    Arguments used on openssl for RSA encryption or decryption.
    Default: '${openssl_rsa_args:-}'

  PRIVATE_KEY
    Path to RSA private key file used for decryption.  Used as -keyin argument
    for openssl pkeyutl.
    Defult: '${PRIVATE_KEY:-}'

  PUBLIC_KEY
    Path to RSA public key file used for encryption.  Used as -keyin argument
    for openssl pkeyutl.
    Defult: '${PUBLIC_KEY:-}'


EXAMPLES

  Generate RSA key pair for examples.

    openssl genrsa -out /tmp/id_rsa 4096
    openssl rsa -in /tmp/id_rsa -pubout -outform pem -out /tmp/id_rsa.pub

  Encrypt data

    echo plaintext | $0 encrypt -o /tmp/cipher.yaml
    $0 decrypt -i output.yaml

  Working with binary data is the same.

    echo plaintext | gzip -9 | $0 encrypt -o /tmp/cipher.yaml
    $0 decrypt -i /tmp/cipher.yaml | gunzip

  Rotate private/public key pair.

    $0 rotate-key -k old-private-key.pem -p new-public-key.pub -f /tmp/cipher.yaml

  Alternate example.

    export PRIVATE_KEY=old-private-key.pem
    export PUBLIC_KEY=new-public-key.pub
    $0 rotate-key -f /tmp/cipher.yaml

  Advanced example using AWS KMS backend for private key.

    url="https://github.com/samrocketman/openssl-engine-kms/releases/download/0.1.1/x86_64-Linux_libopenssl_engine_kms.so.gz"
    curl -sSfL "\$url" | gunzip > libopenssl_engine_kms.so
    export openssl_rsa_args='-keyform engine -engine kms -pkeyopt rsa_padding_mode:oaep -pkeyopt rsa_oaep_md:SHA256'
    export PRIVATE_KEY=arn:aws:kms:us-east-1:111122223333:key/deadbeef-dead-dead-dead-deaddeafbeef
    export PUBLIC_KEY=arn:aws:kms:us-east-1:111122223333:key/deadbeef-dead-dead-dead-deaddeafbeef

    echo hello | $0 encrypt

  Advanced example using RSA public key to encrypt and AWS KMS to decrypt.

    export kms_openssl_rsa_args='-keyform engine -engine kms -pkeyopt rsa_padding_mode:oaep -pkeyopt rsa_oaep_md:SHA256'
    export PRIVATE_KEY=arn:aws:kms:us-east-1:111122223333:key/deadbeef-dead-dead-dead-deaddeafbeef
    export PUBLIC_KEY=/tmp/id_rsa.pub

    echo hello | $0 encrypt | \\
      openssl_rsa_args="\$kms_openssl_rsa_args" $0 decrypt -s openssl_rsa_args


OLD OPENSSL NOTICE

  Old OpenSSL versions before OpenSSL 3.2 do not have -saltlen option
  available.  You must set a few environment variables in order for
  $0 to be compatible with older OpenSSL releases.

    openssl_saltlen=8
    openssl_aes_args='-aes-256-cbc -pbkdf2 -iter 600000'
    export openssl_saltlen openssl_aes_args
    echo plaintext | $0 encrypt -o /tmp/cipher.yaml

  You can upgrade the encryption if migrating to OpenSSL 3.2 or later.  Note
  the old and new file names must be different.  Also note that openssl_saltlen
  and openssl_aes_args environment variables are prefixed on the first command
  and not exported to the second command.

    openssl_saltlen=8 openssl_aes_args='-aes-256-cbc -pbkdf2 -iter 600000' \\
      $0 decrypt -i cipher.yaml -k id_rsa | \\
      $0 encrypt -p id_rsa.pub -o new-cipher.yaml
    mv new-cipher.yaml cipher.yaml

  For even older OpenSSL, you might not want to use
  RSA/ECB/OAEPWithSHA-256AndMGF1Padding and instead use RSA/ECB/PKCS1Padding.
  You can accomplish this by overriding openssl_rsa_args with an empty space.
  Note the space is required so that the veriable is non-zero length.

    export openssl_rsa_args=' '
    echo hello | $0 encrypt


ALGORITHMS

  SHA-256 for data integrity verification.
  RSA/ECB/OAEPWithSHA-256AndMGF1Padding for asymmetric encryption storage.
  AES/CBC/PKCS5Padding for symmetric encryption storage.
  PBKDF2WithHmacSHA256 for key derivation; 600k iterations with 16-byte salt.

SOURCE
  Created by Sam Gleske
  https://github.com/samrocketman/repository-secrets
EOF
exit 1
}

process_arguments() {
  while [ "$#" -gt 0 ]; do
    case "${1}" in
      -o|--output)
        output_file="${2:-}"
        shift
        shift
        ;;
      -k|--private-key)
        PRIVATE_KEY="${2:-}"
        shift
        shift
        ;;
      -p|--public-key)
        PUBLIC_KEY="${2:-}"
        shift
        shift
        ;;
      -i|--in-file)
        input_file="${2:-}"
        shift
        shift
        ;;
      -f|--input-output-file)
        input_file="${2:-}"
        output_file="${2:-}"
        shift
        shift
        ;;
      -s|--skip-field)
        skip_fields+=( "$2" )
        shift
        shift
        ;;
      -h|--help|help)
        helptext
        ;;
      *)
        if [ -z "${sub_command:-}" ]; then
          sub_command="$1"
          shift
        else
          echo 'Unknown option: '"$1" >&2
          echo >&2
          echo 'See also '"$0"' help.' >&2
          exit 1
        fi
    esac
  done
  case "${sub_command:-}" in
    encrypt|decrypt|rotate-key)
      ;;
    *)
      echo 'Must use one of the following subcommands.' >&2
      echo '  - '"$0 encrypt [options]" >&2
      echo '  - '"$0 decrypt [options]" >&2
      echo '  - '"$0 rotate-key [options]" >&2
      echo >&2
      echo 'See also '"$0"' help.' >&2
      exit 1
      ;;
  esac
}

validate_arguments() {
  result=0
  if [ "$sub_command" = encrypt ]; then
    if [ ! -f "${PUBLIC_KEY:-}" ] && ! grep -F :kms: <<< "${PUBLIC_KEY:-}" > /dev/null; then
      echo 'Warning: RSA public key does not exist.' >&2
    fi
  elif [ "$sub_command" = decrypt ]; then
    if [ ! -f "${PRIVATE_KEY:-}" ] && ! grep -F :kms: <<< "${PRIVATE_KEY:-}" > /dev/null; then
      echo 'Warning: RSA private key does not exist.' >&2
    fi
  elif [ "$sub_command" = 'rotate-key' ]; then
    if [ ! -f "${PUBLIC_KEY:-}" ] && ! grep -F :kms: <<< "${PUBLIC_KEY:-}" > /dev/null; then
      echo 'Warning: RSA public key does not exist.' >&2
    fi
    if [ ! -f "${PRIVATE_KEY:-}" ] && ! grep -F :kms: <<< "${PRIVATE_KEY:-}" > /dev/null; then
      echo 'Warning: RSA private key does not exist.' >&2
    fi
    if [ ! "x$input_file" = "x$output_file" ]; then
      echo 'Input-output mismatch.  Use -f FILE option.' >&2
      result=1
    fi
    if [ "x$input_file" = 'x-' ]; then
      echo 'No file selected for key rotation. Use -f FILE option.' >&2
      result=1
    fi
  fi
  if [ ! "x$input_file" = 'x-' ] && [ ! -f "$input_file" ]; then
    echo '-f FILE does not exist: '"'$input_file'" >&2
    result=1
  fi
  if [ ! "$result" = 0 ]; then
    echo >&2
    echo 'See also '"$0"' help.' >&2
  fi
  return "$result"
}

# functions
randompass() (
  set +o pipefail
  LC_ALL=C tr -dc -- "-'"'_!@#$%^&*(){}|[]\;:",./<>?0-9a-fA-F' < /dev/urandom | head -c128
)

randomsalt() (
  set +o pipefail
  local hexbytes="$(( $openssl_saltlen * 2 ))"
  LC_ALL=C tr -dc '0-9a-f' < /dev/urandom | head -c"$hexbytes"
)

stdin_aes_encrypt() {
  openssl enc \
    ${openssl_aes_args} \
    -S "$(<"${TMP_DIR}"/salt)" \
    -pass file:"${TMP_DIR}"/passin \
    -a
}

stdin_aes_decrypt() {
  openssl enc \
    ${openssl_aes_args} \
    -S "$(<"${TMP_DIR}"/salt)" \
    -pass file:"${TMP_DIR}"/passin \
    -a -d
}

stdin_rsa_encrypt() {
  openssl pkeyutl ${openssl_rsa_args} -encrypt -inkey "${PUBLIC_KEY}" -pubin | openssl enc -base64
}

stdin_rsa_decrypt() {
  openssl enc -base64 -d | openssl pkeyutl ${openssl_rsa_args} -decrypt -inkey "${PRIVATE_KEY}"
}

data_or_file() {
  if [ "x${input_file:-}" = 'x-' ]; then
    cat
  else
    cat "${input_file}"
  fi
}

stdin_shasum() {
  (
    if type -P shasum &> /dev/null; then
      shasum -a 256 "$@"
    elif type -P sha256sum &> /dev/null; then
      sha256sum "$@"
    else
      echo 'No sha256sum utility available' >&2
      exit 1
    fi
  )
}

read_yaml_for_hash() {
  yq e '.openssl_aes_args, .openssl_rsa_args, .salt, .passin, .data' "$1"
}

validate_hash() {
  yq '.hash' "$1" \
    | stdin_rsa_decrypt > "${TMP_DIR}"/hash
  read_yaml_for_hash "$1" \
    | stdin_shasum -c "${TMP_DIR}"/hash
}

create_hash() {
  output="${1%.yaml}"_hash.yaml
cat > "$output" <<EOF
hash: |-
$(read_yaml_for_hash "$1" | stdin_shasum | stdin_rsa_encrypt | sed 's/^/  /')
EOF
}

write_to_output() {
  if [ "x${output_file:-}" = 'x-' ]; then
    cat
  else
    cat > "$output_file"
  fi
}

encrypt_file() {
  if [ -f "$output_file" ] && validate_hash "$output_file" &> /dev/null; then
    cp "$output_file" "${TMP_DIR}"/output.yaml
    yq '.salt' "${TMP_DIR}"/output.yaml | stdin_rsa_decrypt > "${TMP_DIR}/salt"
    yq '.passin' "${TMP_DIR}"/output.yaml | stdin_rsa_decrypt > "${TMP_DIR}/passin"
    yq -i 'del(.data)' "${TMP_DIR}"/output.yaml
    yq -i 'del(.hash)' "${TMP_DIR}"/output.yaml
cat > "${TMP_DIR}"/cipher_encrypt.yaml <<EOF
$(cat "${TMP_DIR}"/output.yaml)
data: |-
$(data_or_file | stdin_aes_encrypt | sed 's/^/  /')
EOF
  else
    randompass > "${TMP_DIR}/passin"
    randomsalt > "${TMP_DIR}/salt"
cat > "${TMP_DIR}"/cipher_encrypt.yaml <<EOF
openssl_aes_args: ${openssl_aes_args}
openssl_rsa_args: ${openssl_rsa_args}
salt: |-
$(stdin_rsa_encrypt < "${TMP_DIR}/salt" | sed 's/^/  /')
passin: |-
$(stdin_rsa_encrypt < "${TMP_DIR}/passin" | sed 's/^/  /')
data: |-
$(data_or_file | stdin_aes_encrypt | sed 's/^/  /')
EOF
  fi

  create_hash "${TMP_DIR}"/cipher_encrypt.yaml
  yq eval-all '. as $item ireduce ({}; . *+ $item)' \
    "${TMP_DIR}"/cipher_encrypt.yaml \
    "${TMP_DIR}"/cipher_encrypt_hash.yaml \
    | write_to_output
}

should_not_skip() {
  local field="$1"
  if [ "${#skip_fields[@]}" = 0 ]; then
    return 0
  fi
  for x in "${skip_fields[@]}"; do
    if [ "${field}" = "${x}" ]; then
      return 1
    fi
  done
  return 0
}

decrypt_file() {
  data_or_file > "${TMP_DIR}"/cipher_decrypt.yaml
  if ! yq '. | keys' "${TMP_DIR}"/cipher_decrypt.yaml &> /dev/null; then
    echo '-f FILE is expected to be YAML but it is not valid YAML.' >&2
    echo 'Invalid yaml: '"'$input_file'" >&2
    exit 1
  fi
  if should_not_skip openssl_aes_args; then
    openssl_aes_args="$(yq '.openssl_aes_args' "${TMP_DIR}"/cipher_decrypt.yaml | head -n1)"
  fi
  if should_not_skip openssl_rsa_args; then
    openssl_rsa_args="$(yq '.openssl_rsa_args' "${TMP_DIR}"/cipher_decrypt.yaml | head -n1)"
  fi
  if ! validate_hash "${TMP_DIR}"/cipher_decrypt.yaml > /dev/null; then
    echo 'Checksum verification failed.  Refusing to decrypt.' >&2
    exit 1
  fi
  yq '.salt' "${TMP_DIR}"/cipher_decrypt.yaml | stdin_rsa_decrypt > "${TMP_DIR}/salt"
  yq '.passin' "${TMP_DIR}"/cipher_decrypt.yaml | stdin_rsa_decrypt > "${TMP_DIR}/passin"
  yq '.data' "${TMP_DIR}"/cipher_decrypt.yaml | stdin_aes_decrypt | write_to_output
}

rotate_key() {
  data_or_file > "${TMP_DIR}"/cipher_decrypt.yaml
  if ! validate_hash "${TMP_DIR}"/cipher_decrypt.yaml > /dev/null; then
    echo 'Checksum verification failed.  Refusing to decrypt.' >&2
    exit 1
  fi
  openssl_aes_args="$(yq '.openssl_aes_args' "${TMP_DIR}"/cipher_decrypt.yaml | head -n1)"
  yq '.salt' "${TMP_DIR}"/cipher_decrypt.yaml | stdin_rsa_decrypt > "${TMP_DIR}/salt"
  yq '.passin' "${TMP_DIR}"/cipher_decrypt.yaml | stdin_rsa_decrypt > "${TMP_DIR}/passin"
  awk '$0 ~ /^data:/ { out="1"; print $0; next }; out == "1" && $0 ~ /^[^ ]/ { exit }; out == "1" { print $0 }' \
    < "${TMP_DIR}"/cipher_decrypt.yaml \
    > "${TMP_DIR}"/data.yaml
cat > "${TMP_DIR}"/cipher_encrypt.yaml <<EOF
openssl_aes_args: ${openssl_aes_args}
salt: |-
$(stdin_rsa_encrypt < "${TMP_DIR}/salt" | sed 's/^/  /')
passin: |-
$(stdin_rsa_encrypt < "${TMP_DIR}/passin" | sed 's/^/  /')
$(cat "${TMP_DIR}"/data.yaml)
EOF
  create_hash "${TMP_DIR}"/cipher_encrypt.yaml
  yq eval-all '. as $item ireduce ({}; . *+ $item)' \
    "${TMP_DIR}"/cipher_encrypt.yaml \
    "${TMP_DIR}"/cipher_encrypt_hash.yaml \
    | write_to_output
}

#
# MAIN
#
process_arguments "$@"
validate_arguments

if [ "${sub_command}" = encrypt ]; then
  encrypt_file
elif [ "${sub_command}" = decrypt ]; then
  decrypt_file
else
  rotate_key
fi
