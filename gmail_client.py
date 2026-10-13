"""Read-only Gmail API client with local OAuth token storage."""

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from google.auth.exceptions import GoogleAuthError
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError


GMAIL_READONLY_SCOPE = "https://www.googleapis.com/auth/gmail.readonly"
GMAIL_MODIFY_SCOPE = "https://www.googleapis.com/auth/gmail.modify"
GMAIL_SEND_SCOPE = "https://www.googleapis.com/auth/gmail.send"
SCOPES = [GMAIL_MODIFY_SCOPE, GMAIL_SEND_SCOPE]


@dataclass(frozen=True)
class GmailListResult:
    messages: list[dict[str, str]]
    complete: bool


class GmailClientError(RuntimeError):
    """Raised when Gmail authentication or API access fails."""


class GmailClient:
    def __init__(
        self,
        credentials_path: str | Path = "credentials.json",
        token_path: str | Path = "token.json",
        service: Any | None = None,
    ) -> None:
        self.credentials_path = Path(credentials_path)
        self.token_path = Path(token_path)
        self._service = service
        self._credentials: Any | None = getattr(service, "_credentials", None) if service else None

    def connect(self) -> None:
        """Authenticate and initialize the Gmail API service."""
        if self._service is not None:
            return

        if not self.credentials_path.is_file():
            raise GmailClientError(
                f"OAuth credentials not found: {self.credentials_path}"
            )

        credentials = None

        if self.token_path.is_file():
            try:
                credentials = Credentials.from_authorized_user_file(
                    str(self.token_path)
                )
            except (ValueError, OSError) as exc:
                raise GmailClientError(
                    "Could not read token.json. Remove the invalid token "
                    "and authenticate again."
                ) from exc

        try:
            if credentials and credentials.expired and credentials.refresh_token:
                try:
                    credentials.refresh(Request())
                    self.token_path.write_text(
                        credentials.to_json(),
                        encoding="utf-8",
                    )
                except GoogleAuthError:
                    credentials = None

            if not credentials or not credentials.valid:
                flow = InstalledAppFlow.from_client_secrets_file(
                    str(self.credentials_path), SCOPES
                )
                credentials = flow.run_local_server(port=0, prompt="select_account")

            self.token_path.write_text(
                credentials.to_json(),
                encoding="utf-8",
            )

            self._credentials = credentials
            self._service = build(
                "gmail",
                "v1",
                credentials=credentials,
                cache_discovery=False,
            )

        except (GoogleAuthError, HttpError, OSError, ValueError) as exc:
            raise GmailClientError(
                "Could not authenticate with Gmail. Check OAuth setup, "
                "network access, and token permissions."
            ) from exc

    def get_user_email(self) -> str:
        """Return the authenticated user email address."""
        self.connect()
        try:
            profile = (
                self._service.users()
                .getProfile(userId="me")
                .execute()
            )
            return profile.get("emailAddress", "")
        except HttpError as exc:
            raise GmailClientError(
                "Failed to retrieve user profile from Gmail."
            ) from exc

    def list_recent_messages(
        self,
        *,
        query: str = "newer_than:7d",
        max_results: int = 10,
    ) -> list[dict[str, str]]:
        """Return message IDs and thread IDs, not full email bodies."""
        if not 1 <= max_results <= 100:
            raise ValueError("max_results must be between 1 and 100")

        self.connect()

        try:
            response = (
                self._service.users()
                .messages()
                .list(
                    userId="me",
                    q=query,
                    maxResults=max_results,
                )
                .execute()
            )
        except HttpError as exc:
            raise GmailClientError("Failed to list Gmail messages.") from exc

        return [
            {
                "id": message["id"],
                "threadId": message.get("threadId", ""),
            }
            for message in response.get("messages", [])
        ]

    def list_messages_paginated(
        self,
        *,
        query: str,
        page_size: int = 100,
        max_total_results: int = 1000,
    ) -> GmailListResult:
        """Fetch Gmail message references up to a total safety limit."""
        if not 1 <= page_size <= 100:
            raise ValueError("page_size must be between 1 and 100")
        if max_total_results < 1:
            raise ValueError("max_total_results must be positive")

        self.connect()

        messages: list[dict[str, str]] = []
        page_token: str | None = None

        try:
            while len(messages) < max_total_results:
                remaining = max_total_results - len(messages)
                request = self._service.users().messages().list(
                    userId="me",
                    q=query,
                    maxResults=min(page_size, remaining),
                    pageToken=page_token,
                )
                response = request.execute()

                for message in response.get("messages", []):
                    messages.append({
                        "id": message["id"],
                        "threadId": message.get("threadId", ""),
                    })

                page_token = response.get("nextPageToken")
                if not page_token:
                    return GmailListResult(messages, complete=True)

            # If the cap is reached, check whether more pages remain.
            return GmailListResult(messages, complete=False)

        except HttpError as exc:
            raise GmailClientError(
                "Failed to list Gmail messages."
            ) from exc

    def get_message(self, message_id: str) -> dict[str, Any]:
        """Retrieve one message in Gmail's full payload format."""
        if not message_id.strip():
            raise ValueError("message_id must not be empty")

        self.connect()

        try:
            return (
                self._service.users()
                .messages()
                .get(
                    userId="me",
                    id=message_id,
                    format="full",
                )
                .execute()
            )
        except HttpError as exc:
            raise GmailClientError(
                f"Failed to retrieve Gmail message {message_id!r}."
            ) from exc

    def get_thread(self, thread_id: str) -> dict[str, Any]:
        """Retrieve a thread and its messages from Gmail."""
        if not thread_id.strip():
            raise ValueError("thread_id must not be empty")

        self.connect()

        try:
            return (
                self._service.users()
                .threads()
                .get(
                    userId="me",
                    id=thread_id,
                    format="full",
                )
                .execute()
            )
        except HttpError as exc:
            raise GmailClientError(
                f"Failed to retrieve Gmail thread {thread_id!r}."
            ) from exc

    def get_attachment(self, message_id: str, attachment_id: str) -> dict[str, Any]:
        """Retrieve attachment metadata and data from Gmail."""
        if not message_id.strip() or not attachment_id.strip():
            raise ValueError("message_id and attachment_id must not be empty")

        self.connect()

        try:
            return (
                self._service.users()
                .messages()
                .attachments()
                .get(
                    userId="me",
                    messageId=message_id,
                    id=attachment_id,
                )
                .execute()
            )
        except HttpError as exc:
            raise GmailClientError(
                f"Failed to retrieve attachment {attachment_id!r} for message {message_id!r}."
            ) from exc

    def has_modify_permission(self) -> bool:
        """Return True if the active Gmail credentials include gmail.modify scope."""
        self.connect()
        creds = getattr(self, "_credentials", None)
        if creds is None and hasattr(self, "_service") and self._service is not None:
            # Check credentials bound to client or service
            for attr in ["_credentials", "credentials"]:
                obj = getattr(self._service, attr, None)
                if obj is not None and hasattr(obj, "scopes"):
                    creds = obj
                    break
        if creds and hasattr(creds, "scopes") and creds.scopes:
            return GMAIL_MODIFY_SCOPE in creds.scopes
        return False

    def mark_as_read(self, message_id: str) -> bool:
        """Remove the UNREAD label from a message in Gmail.

        Requires explicit gmail.modify scope. Idempotent on already-read or missing messages.
        """
        if not message_id.strip():
            raise ValueError("message_id must not be empty")

        self.connect()

        if not self.has_modify_permission():
            raise GmailClientError(
                "Missing gmail.modify authorization. Client is currently authorized for read-only access."
            )

        try:
            self._service.users().messages().modify(
                userId="me",
                id=message_id,
                body={"removeLabelIds": ["UNREAD"]},
            ).execute()
            return True
        except HttpError as exc:
            # Idempotent: 404 message already removed in Gmail is safe
            if getattr(exc, "resp", None) and getattr(exc.resp, "status", None) == 404:
                return True
            raise GmailClientError(
                f"Failed to remove UNREAD label from Gmail message {message_id!r}: {exc}"
            ) from exc

    def has_send_permission(self) -> bool:
        """Return True if the active Gmail credentials include gmail.send or gmail.modify or mail.google.com."""
        self.connect()
        creds = getattr(self, "_credentials", None)
        if creds is None and hasattr(self, "_service") and self._service is not None:
            for attr in ["_credentials", "credentials"]:
                obj = getattr(self._service, attr, None)
                if obj is not None and hasattr(obj, "scopes"):
                    creds = obj
                    break
        if creds and hasattr(creds, "scopes") and creds.scopes:
            return any(s in creds.scopes for s in [GMAIL_SEND_SCOPE, GMAIL_MODIFY_SCOPE, "https://mail.google.com/"])
        return False

    def create_draft(
        self,
        to: str,
        subject: str,
        body: str,
        thread_id: str | None = None,
    ) -> dict[str, Any]:
        """Create a draft email in Gmail without sending it."""
        from email.message import EmailMessage
        import base64

        self.connect()
        msg = EmailMessage()
        msg.set_content(body)
        msg["To"] = to
        msg["Subject"] = subject

        encoded = base64.urlsafe_b64encode(msg.as_bytes()).decode("utf-8")
        payload: dict[str, Any] = {"raw": encoded}
        if thread_id:
            payload["threadId"] = thread_id

        try:
            draft = (
                self._service.users()
                .drafts()
                .create(userId="me", body={"message": payload})
                .execute()
            )
            return {
                "draft_id": draft.get("id", ""),
                "message_id": draft.get("message", {}).get("id", ""),
                "thread_id": draft.get("message", {}).get("threadId", "") or thread_id or "",
                "to": to,
                "subject": subject,
                "body": body,
                "status": "draft_created",
            }
        except HttpError as exc:
            raise GmailClientError(f"Failed to create Gmail draft: {exc}") from exc

    def send_message(
        self,
        to: str,
        subject: str,
        body: str,
        thread_id: str | None = None,
        draft_id: str | None = None,
    ) -> dict[str, Any]:
        """Send an email via Gmail API. If draft_id is supplied, sends the existing draft."""
        from email.message import EmailMessage
        import base64

        self.connect()
        if not self.has_send_permission():
            raise GmailClientError(
                "Missing gmail.send authorization. Please re-authenticate with send permission."
            )

        try:
            if draft_id:
                sent = (
                    self._service.users()
                    .drafts()
                    .send(userId="me", body={"id": draft_id})
                    .execute()
                )
            else:
                msg = EmailMessage()
                msg.set_content(body)
                msg["To"] = to
                msg["Subject"] = subject
                encoded = base64.urlsafe_b64encode(msg.as_bytes()).decode("utf-8")
                payload: dict[str, Any] = {"raw": encoded}
                if thread_id:
                    payload["threadId"] = thread_id
                sent = (
                    self._service.users()
                    .messages()
                    .send(userId="me", body=payload)
                    .execute()
                )

            return {
                "id": sent.get("id", ""),
                "thread_id": sent.get("threadId", "") or thread_id or "",
                "to": to,
                "subject": subject,
                "status": "sent",
            }
        except HttpError as exc:
            raise GmailClientError(f"Failed to send email via Gmail: {exc}") from exc


