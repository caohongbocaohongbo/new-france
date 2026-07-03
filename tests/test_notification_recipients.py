import unittest

from backend.agents.layer3_recommendation.notifier import _resolve_recipient_list
from backend.services.runtime_config import ConfigValidationError, _default_config, validate_config


class NotificationRecipientsTest(unittest.TestCase):
    def test_resolve_recipient_list_keeps_default_first_and_deduplicates(self):
        config = {
            "email_user": "896256756@qq.com",
            "email_to": "backup@example.com",
            "email_recipients": [
                "",
                "alpha@example.com",
                "backup@example.com",
                "alpha@example.com",
                "beta@example.com",
            ],
        }

        recipients = _resolve_recipient_list(config)

        self.assertEqual(
            recipients,
            ["backup@example.com", "alpha@example.com", "beta@example.com"],
        )

    def test_resolve_recipient_list_falls_back_to_sender_when_primary_missing(self):
        config = {
            "email_user": "896256756@qq.com",
            "email_to": "",
            "email_recipients": ["foo@example.com", "", "bar@example.com"],
        }

        recipients = _resolve_recipient_list(config)

        self.assertEqual(
            recipients,
            ["896256756@qq.com", "foo@example.com", "bar@example.com"],
        )

    def test_validate_config_rejects_more_than_ten_total_recipients(self):
        payload = _default_config()
        payload["notification"].update(
            {
                "emailEnabled": True,
                "emailUser": "896256756@qq.com",
                "emailTo": "",
                "recipients": [f"user{i}@example.com" for i in range(10)],
            }
        )

        with self.assertRaisesRegex(ConfigValidationError, "收件箱数量不能超过 10 个"):
            validate_config(payload)

    def test_validate_config_rejects_invalid_recipient_email(self):
        payload = _default_config()
        payload["notification"].update(
            {
                "emailEnabled": True,
                "emailUser": "896256756@qq.com",
                "emailTo": "",
                "recipients": ["bad-email"],
            }
        )

        with self.assertRaisesRegex(ConfigValidationError, "收件邮箱 格式不正确"):
            validate_config(payload)

    def test_validate_config_accepts_up_to_nine_extra_recipients(self):
        payload = _default_config()
        payload["notification"].update(
            {
                "emailEnabled": True,
                "emailUser": "896256756@qq.com",
                "emailTo": "",
                "recipients": [
                    " alpha@example.com ",
                    "beta@example.com",
                    "alpha@example.com",
                    "",
                ],
            }
        )

        config = validate_config(payload)

        self.assertEqual(
            config["notification"]["recipients"],
            ["alpha@example.com", "beta@example.com"],
        )


if __name__ == "__main__":
    unittest.main()
