from unittest.mock import MagicMock

import pytest

from gmail_client import GmailClient, GmailClientError


@pytest.fixture
def mock_service():
    service = MagicMock()
    return service


def test_list_recent_messages_returns_ids(mock_service, tmp_path):
    mock_service.users.return_value.messages.return_value.list.return_value.execute.return_value = {
        "messages": [
            {"id": "msg-1", "threadId": "thread-1"},
            {"id": "msg-2", "threadId": "thread-2"},
        ]
    }

    client = GmailClient(
        credentials_path=tmp_path / "credentials.json",
        token_path=tmp_path / "token.json",
        service=mock_service,
    )

    messages = client.list_recent_messages(max_results=2)

    assert messages == [
        {"id": "msg-1", "threadId": "thread-1"},
        {"id": "msg-2", "threadId": "thread-2"},
    ]


def test_list_recent_messages_handles_empty_inbox(mock_service, tmp_path):
    mock_service.users.return_value.messages.return_value.list.return_value.execute.return_value = {}

    client = GmailClient(
        credentials_path=tmp_path / "credentials.json",
        token_path=tmp_path / "token.json",
        service=mock_service,
    )

    assert client.list_recent_messages() == []


@pytest.mark.parametrize("limit", [0, -1, 101])
def test_list_recent_messages_rejects_invalid_limit(
    mock_service, tmp_path, limit
):
    client = GmailClient(
        credentials_path=tmp_path / "credentials.json",
        token_path=tmp_path / "token.json",
        service=mock_service,
    )

    with pytest.raises(ValueError, match="max_results"):
        client.list_recent_messages(max_results=limit)


def test_get_message_rejects_empty_id(mock_service, tmp_path):
    client = GmailClient(
        credentials_path=tmp_path / "credentials.json",
        token_path=tmp_path / "token.json",
        service=mock_service,
    )

    with pytest.raises(ValueError, match="message_id"):
        client.get_message("   ")


def test_get_message_returns_full_payload(mock_service, tmp_path):
    expected = {
        "id": "msg-1",
        "payload": {"mimeType": "text/plain", "headers": []},
    }
    mock_service.users.return_value.messages.return_value.get.return_value.execute.return_value = expected

    client = GmailClient(
        credentials_path=tmp_path / "credentials.json",
        token_path=tmp_path / "token.json",
        service=mock_service,
    )

    assert client.get_message("msg-1") == expected


def test_list_messages_paginated_multiple_pages(mock_service, tmp_path):
    page1 = {
        "messages": [{"id": "msg-1", "threadId": "t-1"}],
        "nextPageToken": "token-2",
    }
    page2 = {
        "messages": [{"id": "msg-2", "threadId": "t-2"}],
    }
    mock_service.users.return_value.messages.return_value.list.return_value.execute.side_effect = [
        page1,
        page2,
    ]

    client = GmailClient(
        credentials_path=tmp_path / "credentials.json",
        token_path=tmp_path / "token.json",
        service=mock_service,
    )

    result = client.list_messages_paginated(
        query="newer_than:7d",
        page_size=1,
        max_total_results=10,
    )

    assert result.complete is True
    assert len(result.messages) == 2
    assert result.messages[0]["id"] == "msg-1"
    assert result.messages[1]["id"] == "msg-2"


def test_list_messages_paginated_reaches_cap_with_more_pages(mock_service, tmp_path):
    page1 = {
        "messages": [
            {"id": "msg-1", "threadId": "t-1"},
            {"id": "msg-2", "threadId": "t-2"},
        ],
        "nextPageToken": "token-2",
    }
    mock_service.users.return_value.messages.return_value.list.return_value.execute.return_value = page1

    client = GmailClient(
        credentials_path=tmp_path / "credentials.json",
        token_path=tmp_path / "token.json",
        service=mock_service,
    )

    # Cap at 2 messages, but there's a nextPageToken
    result = client.list_messages_paginated(
        query="newer_than:7d",
        page_size=2,
        max_total_results=2,
    )

    assert result.complete is False
    assert len(result.messages) == 2


def test_list_messages_paginated_validates_arguments(mock_service, tmp_path):
    client = GmailClient(
        credentials_path=tmp_path / "credentials.json",
        token_path=tmp_path / "token.json",
        service=mock_service,
    )

    with pytest.raises(ValueError, match="page_size"):
        client.list_messages_paginated(query="newer_than:7d", page_size=0)

    with pytest.raises(ValueError, match="max_total_results"):
        client.list_messages_paginated(query="newer_than:7d", max_total_results=0)


def test_get_user_email_returns_address(mock_service, tmp_path):
    mock_service.users.return_value.getProfile.return_value.execute.return_value = {
        "emailAddress": "primary@example.com",
    }
    client = GmailClient(
        credentials_path=tmp_path / "credentials.json",
        token_path=tmp_path / "token.json",
        service=mock_service,
    )
    assert client.get_user_email() == "primary@example.com"


