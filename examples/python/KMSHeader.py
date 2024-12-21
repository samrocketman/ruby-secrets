"""
KMS header is a format for binary blob data which was encrypted with KMS.

For encryption,
  pip install cryptography

For decryption,
  pip install boto3

Algorithm:
  RSA (2048, 3072, or 4096) with OAEP SHA1 or OAEP SHA256 padding

Author:
  Proposal by Sam Gleske
  Copyright (c) 2015-2024 Sam Gleske - https://github.com/samrocketman/repository-secrets
  MIT Licensed

Proposal:
  For applications where data encrypted at rest is a requirement.  This class
  proposes the concept of an asymmetric KMS header. The encrypted data to be
  symmetrically encrypted and the keys to decrypt are stored by encrypting them
  with an asymmetric public key.  The KMS header would be at the beginning of
  symmetrically encrypted data.

More about the KMS header:
  Generically, a KMS header is an ARN, with the asymmetric algorithm, with the
  asymmetrically encrypted cipher data.  This class is not responsible for
  symmetric decryption.  It stores and decrypts symmetric keys from asymmetric
  encrypted data.

  A KMS header is a KMS ARN, asymmetric algorithms used to encrypt, and cipher
  text as a contiguous piece of binary data.  When writing encrypted data, the
  KMS header must be written first followed by the symmetrically encrypted
  data.

Developer use case:
  On a front-end system, encrypt data symmetrically and store the information
  necessary to decrypt as KMS header.  The data will be secured in any data
  storage.  The front-end system can only encrypt.  The public key can be
  stored within the application to reduce KMS API calls.  KMS need not be used
  for encryption operations.

  A backend system can use the KMS Header to decrypt with the private key using
  KMS.  Because the main portion of the data is encrypted symmetrically,
  there's a cost savings with reduced KMS API calls decrypting small amounts of
  data.  KMS is not used for symmetric decryption.

  Partial inspection of binary blobs supported which enables key rotation.

  The first 16 bytes of the KMS header is the KMS key ID.  To determine if a
  key is rotated on all binary blobs you need only inspect the first 16 bytes.

  To determine if a specific AWS account ID is in use you can read the first 32
  bytes.  The first 16 bytes is the KMS key ID and the second 16 bytes is the
  AWS account ID.

Binary Format:
  First 35 bytes (KMS ARN):
    16 bytes = KMS Key ID
    16 bytes = AWS Account ID
    3 bytes = AWS Region

  1 byte algorithm: RSA_2048 (0x01), RSA_3072 (0x02), RSA_4096 (0x03)

  Followed 256-512 bytes of RSA cipher data. (still part of the KMS header)

  Followed by symmetrically encrypted data. (not part of the KMS header)

  See also __len__(self) decription.

Examles:
  Empty example
    header = KMSHeader()
    header.add_arn("arn:...")
    header.add_algorithm("RSA_...")
    header.add_cipher_data(rsa_encrypted_binary_data)
    header.to_binary()
    header.to_base64()
    len(header) # get current binary header length
  Instantiate a KMS Header.
    header = KMSHeader("arn:...")
    header = KMSHeader("arn:...", algorithm, key_spec)
    header = KMSHeader(kms_header_binary_data)
    header = KMSHeader.from_base64(kms_header_binary_data_base64_encoded)
  Add encrypted data to KMS Header.
    header.add_cipher_data(rsa_encrypted_binary_data)
  Export a KMS Header.
    header.to_binary()
    header.to_base64()
  Extract information from KMS Header.
    header.get_arn()
    header.get_algorithm()
    header.get_cipher_data()
  Working with decryption:
    kms_information = KMSHeader().get_partial_kms_header(encrypted_binary[:36])
    header = KMSHeader(encrypted_binary)
    symmetric_keys = header.decrypt()
    symmetric_ciphertext = encrypted_binary[len(header):]
  Work with encryption:
    header = KMSHeader("arn:...")
    header.add_public_key(pem_encoded_rsa_public_key)
    header.encrypt(symmetric_keys)
    header.to_binary() + symmetric_ciphertext
"""

import base64
import binascii
import os
import re

# optional RSA encrypt
try:
    from cryptography.hazmat.primitives import asymmetric
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives import serialization
except ModuleNotFoundError:
    pass

# optional decrypt with KMS
try:
    import boto3
except ModuleNotFoundError:
    pass


class KMSHeader:
    """Creates an instance of a KMS header.

    Algorithms:
      RSAES_OAEP_SHA_1
      RSAES_OAEP_SHA_256

    Key specs:
      RSA_2048
      RSA_3072
      RSA_4096

    Args:
      arn_or_header: Can be a KMS ARN (str) or binary KMS header.
      algorithm: A supported algorithm KMS would use to decrypt.
      key_spec: A supported key spec KMS would store.

    Raises:
      ValueError: If any argument provided is not valid.
    """

    # 3-byte region, 16-byte AWS account ID, 16-byte kms key ID, 512-byte RSA cipher text (4096-bit key), AES cipher data unlimited
    # region = data[:3]
    # account = data[4:20]
    # kms_id = data[20:35]
    # rsa_ciphertext = data[35:548]
    # aes_ciphertext = data[548:] I would use AES-CBC or AES-GCM or ChaCha20-Poly1305
    major_region = {
        "af": "00",
        "ap": "01",
        "ca": "02",
        "eu": "03",
        "il": "04",
        "me": "05",
        "sa": "06",
        "us": "07",
        "us-gov": "08",
    }
    cardinal_endpoint = {
        "north": "00",
        "east": "01",
        "south": "02",
        "west": "03",
        "central": "04",
        "northeast": "05",
        "southeast": "06",
        "southwest": "07",
        "northwest": "08",
    }
    algorithms = {
        "RSA_2048": "01",
        "RSA_3072": "02",
        "RSA_4096": "03",
        "RSAES_OAEP_SHA_1": "10",
        "RSAES_OAEP_SHA_256": "20",
    }
    algorithms_byte_size = {
        "RSAES_OAEP_SHA_1": 42,
        "RSAES_OAEP_SHA_256": 66,
    }
    key_specs_byte_size = {"RSA_2048": 256, "RSA_3072": 384, "RSA_4096": 512}

    # binary data which was RSA encrypted
    arn_regex = r"^arn:aws:kms:([^:]+):([^:]+):key/([-0-9a-f]{36})$"

    def __init__(
        self, arn_or_header=None, algorithm="RSAES_OAEP_SHA_256", key_spec=None
    ):
        self.algorithm = None
        self.arn = None
        self.cipher_data = None
        self.key_spec = None
        self.public_key = None
        hash_algs = ["RSAES_OAEP_SHA_1", "RSAES_OAEP_SHA_256"]
        key_specs = ["RSA_2048", "RSA_3072", "RSA_4096"]
        if algorithm not in hash_algs:
            raise ValueError("algorithm must be one of: %s" % ", ".join(hash_algs))
        if key_spec is not None and key_spec not in key_specs:
            raise ValueError("key_spec must be one of: %s" % ", ".join(key_specs))
        self.add_algorithm(algorithm)
        self.add_algorithm(key_spec)
        if isinstance(arn_or_header, str):
            self.add_arn(arn_or_header)
            return
        if arn_or_header is None:
            self.arn = None
            return
        if not isinstance(arn_or_header, bytes):
            raise ValueError(
                "arn_or_header must be an ARN string or a binary KMS Header."
            )
        data_size = len(arn_or_header)
        if data_size < 35:
            raise ValueError(
                "arn_or_header must be 35-bytes or larger when not type string."
            )
        if data_size >= 36:
            arn_data = binascii.hexlify(arn_or_header[:36]).decode()
        else:
            arn_data = binascii.hexlify(arn_or_header[:35]).decode()
        # assume binary data
        self.arn = self.__hex_to_kms_arn(arn_data[:70])
        if data_size >= 36:
            self.__add_algorithm_hex(arn_data[70:])
        if self.key_spec is None:
            return
        max_header_bytes = 36 + self.__get_key_bytes()
        if data_size >= max_header_bytes:
            self.cipher_data = arn_or_header[36:max_header_bytes]

    def __len__(self):
        """Get the current size in bytes of the binary KMS Header data.

        KMS Header size can vary:
          0 bytes = User code must provide ARN, algorithm, and cipher data.
          35 bytes = Just ARN; user code must provide algorithm and cipher data.
          36 bytes = ARN with KMS algorithm; user code must provide cipher data.
          292 bytes = ARN with RSA_2048 and encrypted data.
          420 bytes = ARN with RSA_3072 and encrypted data.
          548 bytes = ARN with RSA_4096 and encrypted data.

        Returns:
          36 + number of bytes that get encrypted by RSA key.
        """
        if self.arn is None:
            return 0
        if self.key_spec is None:
            return 35
        if self.cipher_data is None:
            return 36
        return 36 + self.__get_key_bytes()

    @classmethod
    def from_base64(cls, b64_data):
        """Create an instance from base64 encoded data

        Args:
          b64_data: KMS Header binary data which was base64 encoded.

        Returns:
          An instance of KMSHeader.

        Raises:
          ValueError: If decode checks do not pass.
        """
        return cls(base64.b64decode(b64_data))

    def to_binary(self):
        """
        Export the current KMS header as binary data.

        The size of the header will vary.  See __len__ for a detailed
        description of different header sizes.

        Returns:
          Binary KMS header.
        """
        header_data = self.__kms_arn_to_bin(self.arn)
        if self.key_spec is not None:
            header_data += self.__algorithm_to_bin()
            if self.cipher_data is not None:
                header_data += self.cipher_data
        return header_data

    def to_base64(self):
        """
        Export the current KMS header as base64 encoded binary data.

        Returns:
          base64 encoded binary KMS header.
        """
        return base64.b64encode(self.to_binary())

    def get_arn(self):
        """Get the KMS ARN stored in the current KMS header.

        Returns:
          A KMS ARN for an RSA key or None if not defined.
        """
        return self.arn

    def get_key_spec(self):
        """Get the KMS key spec stored in the current KMS header.

        Returns:
          A KMS key spec for an RSA key or None if not defined.
        """
        return self.key_spec

    def get_algorithm(self):
        """Get the KMS algorithm stored in the current KMS header.

        Returns:
          A KMS algorithm for an RSA key or None if not defined.
        """
        return self.algorithm

    def add_cipher_data(self, cipher_data):
        """Add RSA encrypted data to KMS Header.

        Args:
          cipher_data: RSA encrypted binary data.

        Raises:
          ValueError: If not exact amount of data required by RSA key size.
        """
        key_size_bytes = self.__get_key_bytes()
        if len(cipher_data) != key_size_bytes:
            raise ValueError(
                "cipher_data was %d bytes but must be exactly %d bytes because key spec is %s."
                % (len(cipher_data), key_size_bytes, self.key_spec)
            )
        self.cipher_data = cipher_data

    def __add_algorithm_hex(self, algorithm_hex):
        alg_id = self.__reghex_to_int(algorithm_hex)
        specs = self.__reghex_to_int("0f")
        algs = self.__reghex_to_int("f0")
        key_spec_id = alg_id & specs
        algorithm_id = alg_id & algs
        if key_spec_id > 0:
            key_spec_hex = self.__regint_to_hex(key_spec_id)
            self.key_spec = self.__key_by_value(self.algorithms, key_spec_hex)
        if algorithm_id > 0:
            alg_hex = self.__regint_to_hex(algorithm_id)
            self.algorithm = self.__key_by_value(self.algorithms, alg_hex)

    def add_algorithm(self, algorithm=None):
        """
        Add an algorithm to the current KMS header.

        Algorithms:
          RSAES_OAEP_SHA_1
          RSAES_OAEP_SHA_256

        Key specs:
          RSA_2048
          RSA_3072
          RSA_4096

        Args:
          algorithm: A supported KMS key spec or algorithm.

        Raises:
          ValueError: If the algorithm passed is not supported.
        """
        if algorithm is None:
            return
        if not isinstance(algorithm, str) or algorithm not in list(
            self.algorithms.keys()
        ):
            raise ValueError(
                "algorithm must be a string.  Value one of: %s"
                % (", ".join(list(self.algorithms.keys())))
            )
        self.__add_algorithm_hex(self.algorithms[algorithm])

    def add_arn(self, arn=None):
        """
        Add a KMS ARN to the current KMS header.

        Args:
          arn: An ARN for a KMS key.

        Raises:
          ValueError: If not a proper KMS ARN format.
        """
        if not isinstance(arn, str) or not re.search(self.arn_regex, arn):
            raise ValueError(
                "arn format does not match.  It must match regex: %s" % self.arn_regex
            )
        backup = self.arn
        try:
            self.arn = arn
            self.to_binary()
        except ValueError:
            self.arn = backup
            raise

    def get_cipher_data(self):
        """
        Get data which was encrypted with an RSA public key.

        Returns:
          Binary cipher data or None.
        """
        return self.cipher_data

    def get_partial_kms_header(self, partial_binary_kms_data):
        """Get information about an encrypted blob using a partial KMS header.

        The intent is to gather high level information about the KMS key used
        to encrypt a blob without actually reading all of the encrypted data.
        For example, S3 allows to partially read objects.

        Args:
          partial_binary_kms_data: The first 16, 32, 35, or 36 bytes of a KMS
          header.

        Returns:
          A dictionary with one or more keys: keyid, account, region, and
          algorithm.
        """
        if not isinstance(partial_binary_kms_data, bytes) or (
            len(partial_binary_kms_data) not in [16, 32, 35, 36]
        ):
            raise ValueError(
                "partial_binary_kms_data is expected to be 16, 32, 35, or 36 bytes."
            )
        data_size = len(partial_binary_kms_data)
        arn_hex = binascii.hexlify(partial_binary_kms_data).decode()
        kms_information = {"keyid": self.__hex_to_keyid(arn_hex[:32])}
        if data_size >= 32:
            kms_information["account"] = self.__hex_to_account(arn_hex[32:64])
        if data_size >= 35:
            kms_information["region"] = self.__hex_to_region(arn_hex[64:70])
        if data_size == 36:
            kms_information["algorithm"] = self.__get_algorithm(arn_hex[70:])
        return kms_information

    def encrypt(self, plain_data):
        """Encrypt data with RSA public key.

        Max data for RSAES_OAEP_SHA_256
          <key size in bits>/8-66 = <data limit in bytes>

        Max data for RSAES_OAEP_SHA_1
          <key size in bits>/8-42 = <data limit in bytes>

        Args:
          plain_data: bytes to be encrypted by RSA.

        Raises:
          TypeError: If plain_data invalid type.
          FileNotFoundError: If public_key not available.
          ValueError: If too much data is provided.
        """
        if not isinstance(plain_data, bytes):
            raise TypeError("plain_data expected to be bytes.")
        if self.public_key is None:
            raise FileNotFoundError("public_key has not be added.  Cannot encrypt.")
        max_data = (
            self.public_key.key_size / 8 - self.algorithms_byte_size[self.algorithm]
        )
        if len(plain_data) > max_data:
            raise ValueError(
                "You attempted to encrypt %d bytes but you cannot encrypt more than %d bytes with %s %s."
                % (len(plain_data), max_data, self.key_spec, self.algorithm)
            )
        hash_algorithm = None
        if self.algorithm == "RSAES_OAEP_SHA_256":
            hash_algorithm = hashes.SHA256()
        elif self.algorithm == "RSAES_OAEP_SHA_1":
            hash_algorithm = hashes.SHA1()
        cipher_data = self.public_key.encrypt(
            plain_data,
            asymmetric.padding.OAEP(
                mgf=asymmetric.padding.MGF1(algorithm=hash_algorithm),
                algorithm=hash_algorithm,
                label=None,
            ),
        )
        self.add_cipher_data(cipher_data)

    def add_public_key(self, public_pem):
        """
        Load an RSA public key so that data can be encrypted.

        Args:
          public_pem: A PEM encoded RSA public key as a string, file path, or already decoded as RSAPublicKey.

        Raises:
          ValueError: When public key does not match a supported algorithm.
          FileNotFoundError: If public_key could not be determined from public_pem.
        """
        backup = self.public_key
        if isinstance(public_pem, asymmetric.rsa.RSAPublicKey):
            self.public_key = public_pem
        elif isinstance(public_pem, str) and "-----BEGIN PUBLIC KEY-----" in public_pem:
            self.public_key = serialization.load_pem_public_key(
                public_pem.encode("utf-8")
            )
        elif os.path.exists(public_pem):
            with open(public_pem, "rb") as key_file:
                self.public_key = serialization.load_pem_public_key(key_file.read())
        else:
            raise ValueError(
                "public_pem does not appear to contain a PEM encoded public key."
            )
        try:
            self.add_algorithm("RSA_%d" % self.public_key.key_size)
        except ValueError:
            self.public_key = backup
            raise

    def decrypt(self):
        """Decrypt the cipher_data using KMS.

        Returns:
          plain data

        Raises:
          ValueError: if arn, agorithm, or cipher_data is None.
        """
        if None in [self.arn, self.key_spec, self.cipher_data]:
            raise ValueError("arn, algorithm, and cihper_data need to be loaded.")
        match = re.search(self.arn_regex, self.arn)
        region = match.group(1)
        kms_client = boto3.client("kms", region_name=region)
        response = kms_client.decrypt(
            KeyId=self.arn,
            CiphertextBlob=self.cipher_data,
            EncryptionAlgorithm=self.algorithm,
        )
        return response["Plaintext"]

    def __algorithm_to_bin(self):
        alg_int = self.__reghex_to_int(self.algorithms[self.algorithm])
        alg_int |= self.__reghex_to_int(self.algorithms[self.key_spec])
        return binascii.unhexlify(self.__regint_to_hex(alg_int))

    def __get_algorithm(self, alg_hex):
        algorithms = []
        alg_id = self.__reghex_to_int(alg_hex)
        specs = self.__reghex_to_int("0f")
        algs = self.__reghex_to_int("f0")
        key_spec_id = alg_id & specs
        algorithm_id = alg_id & algs
        if key_spec_id > 0:
            algorithms.append(
                self.__key_by_value(self.algorithms, self.__regint_to_hex(key_spec_id))
            )
        if algorithm_id:
            algorithms.append(
                self.__key_by_value(self.algorithms, self.__regint_to_hex(algorithm_id))
            )
        return algorithms

    def __get_key_bytes(self):
        return self.key_specs_byte_size[self.key_spec]

    def __key_by_value(self, dictionary, value):
        return list(dictionary.keys())[list(dictionary.values()).index(value)]

    # last byte is regional integer
    def __regint_to_hex(self, region_int, desired_size=2):
        region_hex = "{0:x}".format(int(region_int))
        buffer = desired_size - len(region_hex)
        if buffer > 0:
            region_hex = ("0" * buffer) + region_hex
        return region_hex

    def __reghex_to_int(self, region_hex):
        return int.from_bytes(binascii.unhexlify(region_hex), "big")

    def __region_to_hex(self, region):
        match = re.search(r"(.*)-([a-z]+)-([0-9]+)", region)
        region_hex = "".join(
            [
                self.major_region[match.group(1)],
                self.cardinal_endpoint[match.group(2)],
                self.__regint_to_hex(match.group(3)),
            ]
        )
        return region_hex

    def __hex_to_region(self, region_hex):
        region = "-".join(
            [
                self.__key_by_value(self.major_region, region_hex[:2]),
                self.__key_by_value(self.cardinal_endpoint, region_hex[2:4]),
                str(self.__reghex_to_int(region_hex[4:])),
            ]
        )
        return region

    def __keyid_to_hex(self, keyid):
        return keyid.replace("-", "")

    def __hex_to_keyid(self, keyid_hex):
        match = re.search(r"^[0-9a-f]{32}$", keyid_hex)
        if not match:
            raise AssertionError(
                "16-byte Key ID as a hex string was expected but not found (32 chars)."
            )
        keyid = "-".join(
            [
                keyid_hex[:8],
                keyid_hex[8:12],
                keyid_hex[12:16],
                keyid_hex[16:20],
                keyid_hex[20:],
            ]
        )
        return keyid

    def __account_to_hex(self, account):
        account_hex = self.__regint_to_hex(account, 32)
        return account_hex

    def __hex_to_account(self, account_hex):
        return str(self.__reghex_to_int(account_hex))

    def __kms_arn_to_hex(self, arn):
        match = re.search(self.arn_regex, arn)
        if not match:
            raise ValueError("KMS arn expected.")
        region = match.group(1)
        account = match.group(2)
        keyid = match.group(3)
        try:
            region = self.__region_to_hex(region)
        except KeyError:
            raise ValueError("An invalid region was provided in the arn.")
        try:
            account = self.__account_to_hex(account)
        except ValueError:
            raise ValueError("An invalid account number was provided in the arn.")
        try:
            keyid = self.__keyid_to_hex(keyid)
        except binascii.Error:
            raise ValueError("An invalid keyid was provided in the arn.")

        arn_hex = "".join(
            [
                keyid,
                account,
                region,
            ]
        )
        return arn_hex

    def __hex_to_kms_arn(self, arn_hex):
        match = re.search(r"^[0-9a-f]{70}$", arn_hex)
        if not match:
            raise ValueError("35-byte arn hex expected (70 chars).")
        arn = "arn:aws:kms:%s:%s:key/%s" % (
            self.__hex_to_region(arn_hex[64:]),
            self.__hex_to_account(arn_hex[32:64]),
            self.__hex_to_keyid(arn_hex[:32]),
        )
        return arn

    def __kms_arn_to_bin(self, arn):
        return binascii.unhexlify(self.__kms_arn_to_hex(arn))
