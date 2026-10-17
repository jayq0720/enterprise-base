"""OpenSSL 独立生成的公开合成向量；不是企业微信线上回调验收。"""

import base64
import json
import time
from pathlib import Path
from unittest import TestCase

from enterprise_base.wecom.crypto import InvalidCallback, decrypt, encrypt, xml_fields


class TestWecomCrypto(TestCase):
    def setUp(self):
        self.vector = json.loads(Path(__file__).with_name("wecom_crypto_data.json").read_text())
        self.config = {k: self.vector[k] for k in ("token", "corpid")}
        self.config["key"] = base64.b64encode(bytes(self.vector["key_bytes"])).decode().rstrip("=")

    def decode(self, **replace):
        args = {k: self.vector[k] for k in ("encrypted", "timestamp", "nonce")}
        args.update(sig=self.vector["signature"], now=int(self.vector["timestamp"]))
        args.update(self.config)
        args.update(replace)
        return decrypt(**args)

    def test_openssl_vector(self):
        raw = self.decode()
        self.assertEqual(raw.decode(), self.vector["plaintext"])
        self.assertEqual(xml_fields(raw)["TaskId"], "synthetic-task")

    def test_authenticated_rejections_are_sanitized(self):
        cases: list[dict[str, object]] = [
            dict(sig="0" * 40),
            dict(now=int(self.vector["timestamp"]) + 301),
            dict(timestamp="invalid"),
            dict(nonce="x" * 129),
            dict(key="wrong"),
            dict(corpid="wrong-corp"),
            dict(encrypted="?"),
            dict(encrypted=""),
        ]
        cases.extend(
            dict(encrypted=v["encrypted"], sig=v["signature"]) for v in self.vector["malformed"]
        )
        for case in cases:
            with self.subTest(case=list(case)):
                with self.assertRaises(InvalidCallback) as error:
                    self.decode(**case)
                self.assertEqual(str(error.exception), "企业微信回调校验失败。")
                self.assertNotIn(self.vector["token"], str(error.exception))

    def test_xml_rejections(self):
        for raw in (
            b"",
            b"x" * 65537,
            b"<other/>",
            b"<xml a='x'/>",
            b"<xml><x>1</x><x>2</x></xml>",
            b"<!DOCTYPE xml [<!ENTITY a 'secret'>]><xml>&a;</xml>",
            b"<xml>\x00</xml>",
            b"<xml>",
        ):
            with self.assertRaises(InvalidCallback):
                xml_fields(raw)
        self.assertEqual(
            xml_fields(b"<xml><Content><![CDATA[synthetic]]></Content></xml>")["Content"],
            "synthetic",
        )

    def test_response_randomization(self):
        message = self.vector["plaintext"].encode()
        first, second = encrypt(message, **self.config), encrypt(message, **self.config)
        self.assertNotEqual(first, second)
        fields = xml_fields(first)
        self.assertEqual(
            decrypt(
                fields["Encrypt"],
                fields["MsgSignature"],
                fields["TimeStamp"],
                fields["Nonce"],
                **self.config,
                now=time.time(),
            ),
            message,
        )
