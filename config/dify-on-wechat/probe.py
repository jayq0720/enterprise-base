"""在未修改的上游文件上运行合成契约探针；不会请求网络或读取环境密钥。"""

import argparse
import ast
import base64
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock
from xml.etree import ElementTree


def extract(root, path, name, scope, methods=None):
    """只加载指定类/方法，避开上游启动、日志和网络副作用。"""
    tree = ast.parse((root / path).read_text(encoding="utf-8"))
    node = next(item for item in tree.body if getattr(item, "name", None) == name)
    if isinstance(node, ast.ClassDef):
        node.decorator_list = []
        if methods:
            node.body = [item for item in node.body if getattr(item, "name", None) in methods]
    exec(compile(ast.Module(body=[node], type_ignores=[]), str(path), "exec"), scope)
    return scope[name]


class Config(dict):
    def get_user_data(self, _user):
        return {}


class Context:
    def __init__(self, kind, content):
        self.type, self.content, self.kwargs = kind, content, {}

    def __getitem__(self, key):
        return self.kwargs[key]

    def __setitem__(self, key, value):
        self.kwargs[key] = value

    def __contains__(self, key):
        return key in self.kwargs

    def get(self, key, default=None):
        return self.kwargs.get(key, default)


class EventContext(dict):
    def __init__(self, _event, values):
        super().__init__(values)

    def is_pass(self):
        return False


def check(root, settings, crypto_runtime):
    expected = {
        "channel_type": "wechatcom_app",
        "dify_app_type": "chatbot",
        "dify_conversation_max_messages": 0,
        "expires_in_seconds": 0,
        "concurrency_in_session": 1,
        "debug": False,
    }
    assert all(settings.get(key) == value for key, value in expected.items()), "入口配置不符合约定"
    assert settings.get("dify_error_reply"), "错误响应须使用固定提示"
    config = Config(settings)
    context_type = SimpleNamespace(
        TEXT="text",
        IMAGE_CREATE="image_create",
        ACCEPT_FRIEND="accept_friend",
        VOICE="voice",
    )
    scope = {
        "conf": lambda: config,
        "Context": Context,
        "ContextType": context_type,
        "Bot": object,
        "Channel": object,
        "logger": Mock(),
        "const": SimpleNamespace(DIFY="dify"),
        "Reply": lambda kind, content: SimpleNamespace(type=kind, content=content),
        "ReplyType": SimpleNamespace(TEXT="text", ERROR="error"),
        "EventContext": EventContext,
        "Event": SimpleNamespace(ON_RECEIVE_MESSAGE=1),
        "PluginManager": lambda: SimpleNamespace(emit_event=lambda event: event),
        "ChatMessage": type(
            "ChatMessage",
            (),
            {
                "__init__": lambda self, msg: None,
                "other_user_nickname": None,
                "from_user_nickname": None,
            },
        ),
        "WeChatClient": object,
        "UNKNOWN_ERROR_MSG": "服务暂时不可用",
    }
    extract(root, "bot/dify/dify_session.py", "DifySession", scope)
    extract(root, "bot/dify/dify_session.py", "DifySessionManager", scope)
    extract(root, "channel/wechatcom/wechatcomapp_message.py", "WechatComAppMessage", scope)
    extract(root, "channel/chat_channel.py", "check_prefix", scope)
    channel_class = extract(
        root, "channel/chat_channel.py", "ChatChannel", scope, {"_compose_context"}
    )
    channel = channel_class()
    channel.user_id = None
    channel.channel_type = "wechatcom_app"
    requests = []

    class ChatClient:
        def __init__(self, _key, _base):
            pass

        def create_chat_message(self, **payload):
            requests.append(payload)
            conversation = payload["conversation_id"] or f"synthetic-{payload['user']}"
            return SimpleNamespace(
                status_code=200,
                json=lambda: {
                    "answer": "合成测试回复",
                    "conversation_id": conversation,
                },
            )

    scope.update(
        ChatClient=ChatClient,
        parse_markdown_text=lambda text: [{"type": "text", "content": text}],
    )
    bot_class = extract(
        root,
        "bot/dify/dify_bot.py",
        "DifyBot",
        scope,
        {
            "__init__",
            "reply",
            "_get_payload",
            "_get_dify_conf",
            "_reply",
            "_handle_chatbot",
        },
    )
    bot_class._get_upload_files = lambda *_args: None

    def send(bot, userid):
        message = SimpleNamespace(
            id="synthetic",
            time=1,
            type="text",
            content="合成测试",
            source=userid,
            target="synthetic-corp",
        )
        message = scope["WechatComAppMessage"](message, None)
        context = channel._compose_context(
            message.ctype, message.content, isgroup=False, msg=message
        )
        reply = bot.reply(context.content, context)
        assert reply.content == "合成测试回复", "上游方法执行失败"

    bot = bot_class()
    send(bot, "synthetic-user-001")
    assert requests[-1]["conversation_id"] == ""
    for _ in range(7):
        send(bot, "synthetic-user-001")
        assert requests[-1]["conversation_id"] == "synthetic-synthetic-user-001", "连续会话被重置"
    assert all(
        item["user"] == "synthetic-user-001" and item["inputs"]["user_id"] == "synthetic-user-001"
        for item in requests
    ), "userid 未原样传递"
    send(bot, "synthetic-user-002")
    assert requests[-1]["conversation_id"] == "", "不同员工共享了会话"
    send(bot_class(), "synthetic-user-001")
    assert requests[-1]["conversation_id"] == "", "须重新核对上游会话持久化行为"

    # 反例证明探针能捕获上游默认的第六条消息重置。
    config.pop("dify_conversation_max_messages")
    default_session = scope["DifySession"]("test", "test", "conversation")
    for _ in range(6):
        default_session.count_user_message()
    assert default_session.get_conversation_id() == "", "上游默认阈值已变化，须复核"
    config.update(settings)
    producer = ast.parse((root / "channel/chat_channel.py").read_text(encoding="utf-8"))
    serial = [
        node
        for node in ast.walk(producer)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "BoundedSemaphore"
    ]
    assert len(serial) == 1
    assert eval(compile(ast.Expression(serial[0].args[0]), "concurrency", "eval"), scope) == 1

    class InvalidSignature(Exception):
        pass

    class InvalidCorp(Exception):
        pass

    if crypto_runtime:
        from wechatpy.enterprise.crypto import WeChatCrypto
        from wechatpy.enterprise.exceptions import InvalidCorpIdException
        from wechatpy.exceptions import InvalidSignatureException

        InvalidSignature, InvalidCorp = (
            InvalidSignatureException,
            InvalidCorpIdException,
        )
        crypto = WeChatCrypto(
            "synthetic-token",
            base64.b64encode(b"x" * 32).decode().rstrip("="),
            "synthetic-corp",
        )
        encrypted = ElementTree.fromstring(
            crypto.encrypt_message("synthetic-echo", "synthetic-nonce", "1")
        )
        echo = encrypted.findtext("Encrypt")
    else:
        crypto = Mock()
        crypto.check_signature.side_effect = InvalidSignature
        crypto.decrypt_message.side_effect = InvalidSignature
        echo = "synthetic-encrypted"
    params = SimpleNamespace(
        msg_signature="invalid-signature",
        timestamp="1",
        nonce="synthetic-nonce",
        echostr=echo,
    )
    web = SimpleNamespace(
        input=lambda: params,
        data=lambda: f"<xml><Encrypt>{echo}</Encrypt></xml>".encode(),
        Forbidden=type("Forbidden", (Exception,), {}),
    )
    callback = SimpleNamespace(crypto=crypto, produce=Mock())
    scope.update(
        web=web,
        WechatComAppChannel=lambda: callback,
        parse_message=Mock(),
        InvalidSignatureException=InvalidSignature,
        InvalidCorpIdException=InvalidCorp,
    )
    query = extract(root, "channel/wechatcom/wechatcomapp_channel.py", "Query", scope)()
    for method in (query.GET, query.POST):
        try:
            method()
        except web.Forbidden:
            pass
        else:
            raise AssertionError("错误签名没有拒绝")
    scope["parse_message"].assert_not_called()
    callback.produce.assert_not_called()
    return {
        "userid_and_conversation": "passed",
        "different_users": "passed",
        "restart_loses_conversation": "confirmed",
        "serial_config": "passed",
        "signature_handler": "passed",
        "real_wechatpy_signature": "passed" if crypto_runtime else "not_run",
        "live_wecom_and_dify": "not_run",
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=Path("/app"))
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument(
        "--crypto-runtime",
        action="store_true",
        help="使用镜像内真实 wechatpy 验证错误签名",
    )
    args = parser.parse_args()
    print(
        json.dumps(
            check(args.source, json.loads(args.config.read_text()), args.crypto_runtime),
            ensure_ascii=False,
        )
    )
