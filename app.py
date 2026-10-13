"""Desktop web application for the Local Mail Agent with Smart Inbox and Personal AI Chat."""

from datetime import datetime, timezone
import json
import logging
import os
from pathlib import Path
import sys
import threading
from typing import Any
import urllib.parse
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from chat import answer_chat_query
from digest import build_daily_digest, format_digest_text
from ingestion import ingest_recent_emails
from llm_client import OllamaClient
from storage import EmailStorage
from worker import process_pending_emails

logger = logging.getLogger("mail_agent_app")

HTML_TEMPLATE = r"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Mail Agent — Personal AI Email Assistant</title>
  <link rel="preconnect" href="https://fonts.googleapis.com">
  <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
  <link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&family=JetBrains+Mono:wght@400;500&display=swap" rel="stylesheet">
  <style>
    :root {
      /* Surfaces & Backgrounds */
      --bg-page: #F8FAFC;
      --bg-surface: #FFFFFF;
      --bg-subtle: #F1F5F9;
      --bg-hover: #F8FAFC;
      --bg-selected: #EEF2FF;
      --bg-active: #E0E7FF;

      /* Borders */
      --border: #E2E8F0;
      --border-subtle: #F1F5F9;
      --border-selected: #C7D2FE;
      --border-focus: #4F46E5;

      /* Typography & Text */
      --text-main: #0F172A;
      --text-secondary: #334155;
      --text-muted: #64748B;
      --text-faint: #94A3B8;

      /* Brand Accent - Focused Violet/Indigo */
      --primary: #4F46E5;
      --primary-hover: #4338CA;
      --primary-active: #3730A3;
      --primary-subtle: #EEF2FF;
      --primary-border: #C7D2FE;

      /* Semantic Status Colors (Restrained, subtle backgrounds) */
      --urgent-bg: #FEF2F2;
      --urgent-text: #DC2626;
      --urgent-border: #FECDD3;

      --warning-bg: #FFFBEB;
      --warning-text: #D97706;
      --warning-border: #FDE68A;

      --success-bg: #ECFDF5;
      --success-text: #059669;
      --success-border: #A7F3D0;

      --info-bg: #F0F9FF;
      --info-text: #0284C7;
      --info-border: #BAE6FD;

      --interview-bg: #F5F3FF;
      --interview-text: #7C3AED;
      --interview-border: #DDD6FE;

      --opportunity-bg: #F0FDF4;
      --opportunity-text: #16A34A;
      --opportunity-border: #BBF7D0;

      /* Refined Shadows */
      --shadow-xs: 0 1px 2px rgba(0, 0, 0, 0.04);
      --shadow-sm: 0 1px 3px 0 rgba(0, 0, 0, 0.05), 0 1px 2px -1px rgba(0, 0, 0, 0.05);
      --shadow-md: 0 4px 6px -1px rgba(0, 0, 0, 0.06), 0 2px 4px -2px rgba(0, 0, 0, 0.04);
      --shadow-lg: 0 10px 15px -3px rgba(0, 0, 0, 0.07), 0 4px 6px -4px rgba(0, 0, 0, 0.03);

      /* Border Radii */
      --radius-sm: 5px;
      --radius-md: 8px;
      --radius-lg: 10px;
      --radius-xl: 14px;
    }

    * { box-sizing: border-box; margin: 0; padding: 0; }
    body {
      font-family: 'Inter', -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif;
      background: var(--bg-page);
      color: var(--text-main);
      height: 100vh;
      overflow: hidden;
      display: flex;
      flex-direction: column;
      -webkit-font-smoothing: antialiased;
      -moz-osx-font-smoothing: grayscale;
    }

    /* Top Navigation Header */
    header {
      background: var(--bg-surface);
      border-bottom: 1px solid var(--border);
      height: 54px;
      padding: 0 1.25rem;
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 1rem;
      z-index: 30;
      flex-shrink: 0;
    }
    .brand {
      display: flex;
      align-items: center;
      gap: 0.75rem;
      font-weight: 600;
      font-size: 0.95rem;
      color: var(--text-main);
      letter-spacing: -0.015em;
    }
    .brand-icon {
      width: 28px;
      height: 28px;
      border-radius: var(--radius-md);
      background: var(--primary);
      color: white;
      display: flex;
      align-items: center;
      justify-content: center;
      box-shadow: 0 2px 5px rgba(79, 70, 229, 0.22);
    }
    .brand-badge {
      font-size: 0.7rem;
      font-weight: 500;
      padding: 0.2rem 0.55rem;
      border-radius: 9999px;
      background: var(--bg-subtle);
      color: var(--text-secondary);
      border: 1px solid var(--border);
      display: inline-flex;
      align-items: center;
      gap: 0.35rem;
    }

    .mode-switcher {
      display: flex;
      background: var(--bg-subtle);
      border: 1px solid var(--border);
      border-radius: var(--radius-lg);
      padding: 3px;
      gap: 3px;
    }
    .mode-btn {
      padding: 0.35rem 0.9rem;
      font-size: 0.8rem;
      font-weight: 500;
      border-radius: var(--radius-md);
      cursor: pointer;
      border: none;
      background: transparent;
      color: var(--text-secondary);
      transition: all 0.15s cubic-bezier(0.4, 0, 0.2, 1);
      display: flex;
      align-items: center;
      gap: 0.45rem;
    }
    .mode-btn svg { width: 15px; height: 15px; }
    .mode-btn:hover {
      color: var(--text-main);
    }
    .mode-btn.active {
      background: var(--bg-surface);
      color: var(--primary);
      font-weight: 600;
      box-shadow: var(--shadow-sm);
    }

    .header-actions {
      display: flex;
      gap: 0.5rem;
      align-items: center;
    }
    .btn {
      display: inline-flex;
      align-items: center;
      gap: 0.45rem;
      padding: 0.4rem 0.75rem;
      border-radius: var(--radius-md);
      font-size: 0.8rem;
      font-weight: 500;
      cursor: pointer;
      border: 1px solid transparent;
      transition: all 0.15s ease;
      background: transparent;
      color: var(--text-secondary);
      line-height: 1.25;
      user-select: none;
    }
    .btn svg { width: 14px; height: 14px; flex-shrink: 0; }
    .btn-primary {
      background: var(--primary);
      color: white;
      border-color: var(--primary);
      box-shadow: var(--shadow-xs);
    }
    .btn-primary:hover {
      background: var(--primary-hover);
      border-color: var(--primary-hover);
    }
    .btn-primary:active {
      background: var(--primary-active);
    }
    .btn-secondary {
      background: var(--bg-surface);
      border-color: var(--border);
      color: var(--text-secondary);
      box-shadow: var(--shadow-xs);
    }
    .btn-secondary:hover {
      background: var(--bg-subtle);
      color: var(--text-main);
      border-color: #CBD5E1;
    }
    .btn-success {
      background: var(--success-bg);
      color: var(--success-text);
      border-color: var(--success-border);
    }
    .btn-success:hover {
      background: #D1FAE5;
    }
    .btn-danger {
      background: var(--urgent-bg);
      color: var(--urgent-text);
      border-color: var(--urgent-border);
    }
    .btn-danger:hover {
      background: #FEE2E2;
    }
    .btn-icon {
      padding: 0.42rem;
      border-radius: var(--radius-md);
      border: 1px solid var(--border);
      background: var(--bg-surface);
      color: var(--text-muted);
      cursor: pointer;
      display: inline-flex;
      align-items: center;
      justify-content: center;
      transition: all 0.15s ease;
      box-shadow: var(--shadow-xs);
    }
    .btn-icon:hover {
      background: var(--bg-subtle);
      color: var(--text-main);
      border-color: #CBD5E1;
    }
    .btn.disabled, .btn:disabled {
      opacity: 0.6;
      cursor: not-allowed;
      pointer-events: none;
    }

    /* Main Container */
    .app-body {
      flex: 1;
      display: flex;
      overflow: hidden;
      position: relative;
    }

    /* 3-Column Layout: Sidebar (1), Email Stream (2), Detail / Chat (3) */
    .sidebar {
      width: 236px;
      background: var(--bg-surface);
      border-right: 1px solid var(--border);
      display: flex;
      flex-direction: column;
      flex-shrink: 0;
      user-select: none;
    }
    .nav-section {
      padding: 0.75rem 0.65rem 0.25rem 0.65rem;
      display: flex;
      flex-direction: column;
      gap: 0.15rem;
    }
    .nav-label {
      font-size: 0.68rem;
      text-transform: uppercase;
      font-weight: 700;
      letter-spacing: 0.05em;
      color: var(--text-faint);
      padding: 0.35rem 0.65rem;
    }
    .nav-item {
      display: flex;
      align-items: center;
      justify-content: space-between;
      padding: 0.45rem 0.65rem;
      border-radius: var(--radius-md);
      font-size: 0.825rem;
      font-weight: 500;
      color: var(--text-secondary);
      cursor: pointer;
      transition: all 0.15s cubic-bezier(0.4, 0, 0.2, 1);
    }
    .nav-item-left {
      display: flex;
      align-items: center;
      gap: 0.6rem;
    }
    .nav-item-left svg { width: 15px; height: 15px; color: var(--text-muted); flex-shrink: 0; transition: color 0.15s ease; }
    .nav-item:hover {
      background: var(--bg-subtle);
      color: var(--text-main);
    }
    .nav-item:hover .nav-item-left svg {
      color: var(--text-secondary);
    }
    .nav-item.active {
      background: var(--primary-subtle);
      color: var(--primary);
      font-weight: 600;
    }
    .nav-item.active .nav-item-left svg {
      color: var(--primary);
    }
    .badge {
      background: var(--bg-subtle);
      padding: 0.12rem 0.45rem;
      border-radius: 9999px;
      font-size: 0.7rem;
      font-weight: 600;
      color: var(--text-muted);
      transition: all 0.15s ease;
    }
    .nav-item.active .badge {
      background: var(--primary-border);
      color: var(--primary);
    }

    .sidebar-footer {
      margin-top: auto;
      border-top: 1px solid var(--border);
      padding: 0.75rem 0.85rem;
      display: flex;
      flex-direction: column;
      gap: 0.4rem;
      font-size: 0.72rem;
      color: var(--text-muted);
      background: #FAFAFC;
    }
    .status-indicator-row {
      display: flex;
      align-items: center;
      gap: 0.45rem;
      font-weight: 500;
      color: var(--text-secondary);
    }
    .status-dot {
      width: 7px;
      height: 7px;
      border-radius: 50%;
      background: #10B981;
      flex-shrink: 0;
    }
    .status-dot.syncing { background: #F59E0B; animation: pulse 1.5s infinite; }
    .status-dot.error { background: #EF4444; }
    .status-dot.idle { background: #10B981; }
    @keyframes pulse { 0% { opacity: 1; } 50% { opacity: 0.35; } 100% { opacity: 1; } }

    /* List Panel */
    .list-panel {
      width: 380px;
      background: var(--bg-surface);
      border-right: 1px solid var(--border);
      display: flex;
      flex-direction: column;
      flex-shrink: 0;
    }
    .list-header {
      padding: 0.75rem 0.95rem;
      border-bottom: 1px solid var(--border);
      display: flex;
      flex-direction: column;
      gap: 0.65rem;
      background: var(--bg-surface);
    }
    .search-box {
      position: relative;
      width: 100%;
    }
    .search-input {
      width: 100%;
      background: var(--bg-page);
      border: 1px solid var(--border);
      border-radius: var(--radius-md);
      padding: 0.45rem 2rem 0.45rem 2.1rem;
      color: var(--text-main);
      font-size: 0.825rem;
      outline: none;
      transition: all 0.15s ease;
    }
    .search-input:focus {
      border-color: var(--primary);
      background: var(--bg-surface);
      box-shadow: 0 0 0 2px var(--primary-subtle);
    }
    .search-icon {
      position: absolute;
      left: 0.65rem;
      top: 50%;
      transform: translateY(-50%);
      color: var(--text-faint);
      display: flex;
      align-items: center;
      pointer-events: none;
    }
    .search-icon svg { width: 14px; height: 14px; }
    .search-clear {
      position: absolute;
      right: 0.65rem;
      top: 50%;
      transform: translateY(-50%);
      color: var(--text-faint);
      cursor: pointer;
      display: none;
      font-size: 1.1rem;
      line-height: 1;
    }
    .search-clear:hover { color: var(--text-main); }

    .list-controls {
      display: flex;
      justify-content: space-between;
      align-items: center;
      font-size: 0.75rem;
      color: var(--text-muted);
    }
    .list-sort-select {
      border: 1px solid var(--border);
      border-radius: var(--radius-sm);
      background: var(--bg-surface);
      padding: 0.22rem 0.45rem;
      font-size: 0.72rem;
      color: var(--text-secondary);
      outline: none;
      cursor: pointer;
      transition: border-color 0.15s ease;
    }
    .list-sort-select:hover {
      border-color: #CBD5E1;
    }

    .email-scroll {
      flex: 1;
      overflow-y: auto;
      padding: 0.5rem;
      display: flex;
      flex-direction: column;
      gap: 0.3rem;
    }
    .email-card {
      background: var(--bg-surface);
      border: 1px solid var(--border-subtle);
      border-radius: var(--radius-lg);
      padding: 0.75rem 0.85rem;
      cursor: pointer;
      transition: all 0.14s cubic-bezier(0.4, 0, 0.2, 1);
      display: flex;
      flex-direction: column;
      gap: 0.35rem;
      position: relative;
    }
    .email-card:hover {
      background: var(--bg-hover);
      border-color: var(--border);
      box-shadow: var(--shadow-xs);
    }
    .email-card.selected {
      background: var(--bg-selected);
      border-color: var(--border-selected);
      box-shadow: var(--shadow-xs);
    }
    .email-card.unhandled::before {
      content: '';
      position: absolute;
      left: 3px;
      top: 14px;
      bottom: 14px;
      width: 3px;
      border-radius: 2px;
      background: var(--primary);
    }

    .card-top-row {
      display: flex;
      justify-content: space-between;
      align-items: baseline;
      gap: 0.5rem;
    }
    .card-sender {
      font-size: 0.825rem;
      font-weight: 600;
      color: var(--text-main);
      overflow: hidden;
      text-overflow: ellipsis;
      white-space: nowrap;
    }
    .card-date {
      font-size: 0.7rem;
      color: var(--text-muted);
      white-space: nowrap;
      flex-shrink: 0;
    }
    .card-subject {
      font-size: 0.825rem;
      font-weight: 500;
      color: var(--text-main);
      line-height: 1.32;
      overflow: hidden;
      text-overflow: ellipsis;
      white-space: nowrap;
    }
    .card-preview {
      font-size: 0.75rem;
      color: var(--text-muted);
      line-height: 1.35;
      overflow: hidden;
      text-overflow: ellipsis;
      white-space: nowrap;
    }
    .card-badges {
      display: flex;
      gap: 0.35rem;
      align-items: center;
      flex-wrap: wrap;
      margin-top: 0.2rem;
    }

    /* Restrained, Minimal Status Tags */
    .tag {
      font-size: 0.65rem;
      font-weight: 500;
      padding: 0.15rem 0.45rem;
      border-radius: var(--radius-sm);
      display: inline-flex;
      align-items: center;
      gap: 0.25rem;
      line-height: 1.2;
    }
    .tag-urgent { background: var(--urgent-bg); color: var(--urgent-text); border: 1px solid var(--urgent-border); }
    .tag-high { background: var(--warning-bg); color: var(--warning-text); border: 1px solid var(--warning-border); }
    .tag-interview { background: var(--interview-bg); color: var(--interview-text); border: 1px solid var(--interview-border); }
    .tag-opportunity { background: var(--opportunity-bg); color: var(--opportunity-text); border: 1px solid var(--opportunity-border); }
    .tag-deadline { background: var(--urgent-bg); color: var(--urgent-text); border: 1px solid var(--urgent-border); font-weight: 600; }
    .tag-subtle { background: var(--bg-subtle); color: var(--text-secondary); border: 1px solid var(--border); }
    .tag-handled { background: var(--success-bg); color: var(--success-text); border: 1px solid var(--success-border); }

    /* Reading Detail Panel */
    .detail-panel {
      flex: 1;
      background: var(--bg-page);
      display: flex;
      flex-direction: column;
      overflow-y: auto;
      padding: 1.5rem 2rem;
      position: relative;
    }
    .detail-empty {
      margin: auto;
      text-align: center;
      color: var(--text-muted);
      display: flex;
      flex-direction: column;
      align-items: center;
      gap: 0.75rem;
      max-width: 360px;
    }
    .detail-empty-icon {
      width: 48px;
      height: 48px;
      border-radius: 50%;
      background: var(--bg-surface);
      border: 1px solid var(--border);
      color: var(--text-faint);
      display: flex;
      align-items: center;
      justify-content: center;
      box-shadow: var(--shadow-xs);
    }
    .detail-empty-icon svg { width: 22px; height: 22px; }

    .detail-container {
      display: flex;
      flex-direction: column;
      gap: 1.15rem;
      max-width: 860px;
      width: 100%;
      margin: 0 auto;
    }
    .detail-card {
      background: var(--bg-surface);
      border: 1px solid var(--border);
      border-radius: var(--radius-lg);
      padding: 1.25rem 1.5rem;
      box-shadow: var(--shadow-xs);
    }
    .detail-header-actions {
      display: flex;
      justify-content: space-between;
      align-items: center;
      gap: 0.75rem;
      padding-bottom: 0.85rem;
      border-bottom: 1px solid var(--border);
      margin-bottom: 1rem;
      flex-wrap: wrap;
    }
    .detail-actions-group {
      display: flex;
      gap: 0.4rem;
      align-items: center;
      flex-wrap: wrap;
    }

    .detail-subject {
      font-size: 1.25rem;
      font-weight: 700;
      color: var(--text-main);
      line-height: 1.3;
      letter-spacing: -0.015em;
      margin-bottom: 0.75rem;
    }
    .detail-meta-grid {
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
      gap: 0.5rem 1rem;
      font-size: 0.8rem;
      color: var(--text-muted);
    }
    .detail-meta-item strong {
      color: var(--text-main);
      font-weight: 500;
    }

    /* Imminent Deadline Alert */
    .deadline-alert {
      background: var(--urgent-bg);
      border: 1px solid var(--urgent-border);
      border-radius: var(--radius-lg);
      padding: 0.85rem 1.15rem;
      display: flex;
      align-items: center;
      gap: 0.85rem;
    }
    .deadline-alert-icon {
      color: var(--urgent-text);
      display: flex;
      align-items: center;
    }
    .deadline-alert-icon svg { width: 22px; height: 22px; }

    /* Action Required Banner */
    .action-required-banner {
      padding: 0.9rem 1.15rem;
      background: var(--primary-subtle);
      border: 1.5px solid var(--primary);
      border-radius: var(--radius-md);
      display: flex;
      justify-content: space-between;
      align-items: center;
      gap: 1rem;
    }
    .action-required-left {
      display: flex;
      align-items: center;
      gap: 0.75rem;
    }
    .action-required-icon {
      width: 34px;
      height: 34px;
      border-radius: 50%;
      background: var(--primary);
      display: flex;
      align-items: center;
      justify-content: center;
      color: #fff;
      flex-shrink: 0;
    }

    /* Resume Skills Match Banner */
    .resume-match-banner {
      padding: 0.65rem 1rem;
      background: var(--opportunity-bg);
      border: 1px solid var(--opportunity-border);
      border-radius: var(--radius-md);
      display: flex;
      align-items: center;
      gap: 0.5rem;
      font-size: 0.8rem;
      color: #065F46;
    }

    /* AI Intelligence Box */
    .ai-card {
      background: var(--bg-surface);
      border: 1px solid var(--border);
      border-radius: var(--radius-lg);
      padding: 1.25rem 1.5rem;
      box-shadow: var(--shadow-xs);
    }
    .ai-card-title {
      font-size: 0.75rem;
      text-transform: uppercase;
      font-weight: 700;
      letter-spacing: 0.05em;
      color: var(--text-secondary);
      margin-bottom: 0.75rem;
      display: flex;
      align-items: center;
      gap: 0.4rem;
    }
    .ai-card-title svg { width: 14px; height: 14px; color: var(--primary); }

    .intel-grid {
      display: grid;
      grid-template-columns: repeat(3, 1fr);
      gap: 0.75rem;
      margin-bottom: 1rem;
    }
    .intel-cell {
      background: var(--bg-page);
      border: 1px solid var(--border);
      border-radius: var(--radius-md);
      padding: 0.65rem 0.85rem;
    }
    .intel-label {
      font-size: 0.68rem;
      color: var(--text-muted);
      text-transform: uppercase;
      font-weight: 600;
      margin-bottom: 0.2rem;
    }
    .intel-value {
      font-size: 0.85rem;
      font-weight: 600;
      color: var(--text-main);
    }

    .recommendation-box {
      background: var(--primary-subtle);
      border: 1px solid var(--primary-border);
      border-radius: var(--radius-md);
      padding: 0.85rem 1rem;
      margin-top: 0.75rem;
    }
    .recommendation-title {
      font-size: 0.72rem;
      text-transform: uppercase;
      font-weight: 700;
      letter-spacing: 0.04em;
      color: var(--primary);
      margin-bottom: 0.35rem;
      display: flex;
      align-items: center;
      gap: 0.35rem;
    }
    .recommendation-text {
      font-size: 0.85rem;
      font-weight: 500;
      color: var(--text-main);
      line-height: 1.45;
    }

    .evidence-item {
      font-family: 'JetBrains Mono', monospace;
      font-size: 0.75rem;
      background: var(--bg-subtle);
      border-left: 3px solid var(--primary);
      padding: 0.35rem 0.65rem;
      border-radius: 0 var(--radius-sm) var(--radius-sm) 0;
      color: var(--text-secondary);
      margin-bottom: 0.35rem;
    }

    /* Email Body Reading Area */
    .email-body-card {
      background: var(--bg-surface);
      border: 1px solid var(--border);
      border-radius: var(--radius-lg);
      padding: 1.25rem 1.5rem;
      box-shadow: var(--shadow-xs);
    }
    .email-body-text {
      font-size: 0.875rem;
      color: var(--text-main);
      line-height: 1.6;
      white-space: pre-wrap;
      word-break: break-word;
    }

    /* Attachments Area */
    .attachment-card {
      display: flex;
      align-items: center;
      justify-content: space-between;
      background: var(--bg-page);
      border: 1px solid var(--border);
      border-radius: var(--radius-md);
      padding: 0.65rem 0.85rem;
      transition: all 0.15s ease;
    }
    .attachment-card:hover {
      border-color: #CBD5E1;
      background: var(--bg-surface);
    }
    .attachment-meta {
      display: flex;
      align-items: center;
      gap: 0.65rem;
    }
    .attachment-icon {
      color: var(--text-muted);
      display: flex;
    }

    /* Thread Timeline */
    .thread-item {
      padding: 0.65rem 0.85rem;
      background: var(--bg-page);
      border: 1px solid var(--border);
      border-radius: var(--radius-md);
      cursor: pointer;
      transition: all 0.15s ease;
    }
    .thread-item:hover {
      border-color: #CBD5E1;
      background: var(--bg-subtle);
    }
    .thread-item.active {
      background: var(--bg-selected);
      border-color: var(--border-selected);
    }

    /* Full Conversational AI Assistant Workspace */
    .chat-view {
      flex: 1;
      display: flex;
      flex-direction: column;
      background: var(--bg-page);
      overflow: hidden;
    }
    .chat-header {
      padding: 0.75rem 1.5rem;
      background: var(--bg-surface);
      border-bottom: 1px solid var(--border);
      display: flex;
      justify-content: space-between;
      align-items: center;
      flex-shrink: 0;
    }
    .chat-context-bar {
      background: var(--primary-subtle);
      border-bottom: 1px solid var(--primary-border);
      padding: 0.5rem 1.5rem;
      display: flex;
      align-items: center;
      justify-content: space-between;
      font-size: 0.78rem;
      color: var(--primary);
    }
    .chat-messages {
      flex: 1;
      overflow-y: auto;
      padding: 1.5rem;
      display: flex;
      flex-direction: column;
      gap: 1.25rem;
      max-width: 860px;
      width: 100%;
      margin: 0 auto;
    }
    .chat-msg {
      display: flex;
      flex-direction: column;
      gap: 0.35rem;
      max-width: 85%;
    }
    .chat-msg.user {
      align-self: flex-end;
    }
    .chat-msg.assistant {
      align-self: flex-start;
      width: 100%;
      max-width: 100%;
    }
    .chat-bubble {
      padding: 0.85rem 1.15rem;
      border-radius: var(--radius-lg);
      font-size: 0.875rem;
      line-height: 1.55;
    }
    .chat-msg.user .chat-bubble {
      background: var(--primary);
      color: white;
      border-bottom-right-radius: 3px;
      box-shadow: 0 1px 3px rgba(79, 70, 229, 0.2);
    }
    .chat-msg.assistant .chat-bubble {
      background: var(--bg-surface);
      border: 1px solid var(--border);
      color: var(--text-main);
      box-shadow: var(--shadow-sm);
      border-bottom-left-radius: 3px;
    }
    .chat-bubble p {
      margin: 0.35rem 0;
    }
    .chat-bubble p:first-child {
      margin-top: 0;
    }
    .chat-bubble p:last-child {
      margin-bottom: 0;
    }
    .chat-bubble h3, .chat-bubble h4 {
      font-size: 0.875rem;
      font-weight: 600;
      color: var(--text-main);
      margin: 0.65rem 0 0.3rem 0;
    }
    .chat-bubble ul, .chat-bubble ol {
      margin: 0.35rem 0 0.45rem 1.25rem;
      padding: 0;
    }
    .chat-bubble li {
      margin-bottom: 0.25rem;
      line-height: 1.5;
    }
    .chat-bubble strong {
      font-weight: 600;
      color: var(--text-main);
    }
    .chat-bubble code {
      background: var(--bg-subtle);
      padding: 0.15rem 0.35rem;
      border-radius: var(--radius-sm);
      font-family: 'JetBrains Mono', monospace;
      font-size: 0.775rem;
    }

    .chat-tool-meta {
      display: inline-flex;
      align-items: center;
      gap: 0.4rem;
      font-size: 0.7rem;
      font-weight: 500;
      padding: 0.2rem 0.5rem;
      border-radius: var(--radius-sm);
      margin-bottom: 0.4rem;
      border: 1px solid var(--border);
      background: var(--bg-page);
      color: var(--text-secondary);
    }
    .freshness-badge {
      display: inline-flex;
      align-items: center;
      gap: 0.3rem;
      font-size: 0.68rem;
      font-weight: 500;
      padding: 0.15rem 0.45rem;
      border-radius: 9999px;
      margin-left: 0.4rem;
    }
    .freshness-badge.gmail { background: var(--success-bg); color: var(--success-text); border: 1px solid var(--success-border); }
    .freshness-badge.cached { background: var(--bg-subtle); color: var(--text-muted); border: 1px solid var(--border); }

    /* Result Card Inside Chat */
    .chat-result-cards {
      display: grid;
      grid-template-columns: repeat(auto-fill, minmax(280px, 1fr));
      gap: 0.65rem;
      margin-top: 0.75rem;
    }
    .chat-result-card {
      background: var(--bg-page);
      border: 1px solid var(--border);
      border-radius: var(--radius-md);
      padding: 0.75rem;
      display: flex;
      flex-direction: column;
      gap: 0.35rem;
      transition: all 0.12s ease;
    }
    .chat-result-card:hover {
      border-color: #CBD5E1;
      background: var(--bg-surface);
      box-shadow: var(--shadow-xs);
    }

    /* Draft Reply Box in Chat */
    .draft-reply-box {
      margin-top: 0.85rem;
      background: var(--bg-page);
      border: 1px solid var(--border);
      border-radius: var(--radius-md);
      padding: 0.85rem;
    }
    .draft-textarea {
      width: 100%;
      min-height: 120px;
      background: var(--bg-surface);
      border: 1px solid var(--border);
      border-radius: var(--radius-sm);
      padding: 0.65rem;
      font-size: 0.825rem;
      font-family: inherit;
      color: var(--text-main);
      resize: vertical;
      outline: none;
      transition: border-color 0.15s ease;
    }
    .draft-textarea:focus { border-color: var(--primary); }

    .chat-prompt-chips {
      display: flex;
      gap: 0.4rem;
      flex-wrap: wrap;
      margin-bottom: 0.75rem;
      max-width: 860px;
      width: 100%;
      margin: 0 auto 0.75rem auto;
    }
    .chip {
      background: var(--bg-surface);
      border: 1px solid var(--border);
      border-radius: 9999px;
      padding: 0.35rem 0.75rem;
      font-size: 0.75rem;
      font-weight: 500;
      color: var(--text-secondary);
      cursor: pointer;
      transition: all 0.15s cubic-bezier(0.4, 0, 0.2, 1);
      box-shadow: var(--shadow-xs);
    }
    .chip:hover {
      background: var(--bg-subtle);
      color: var(--primary);
      border-color: var(--primary-border);
    }

    .chat-input-bar {
      padding: 1rem 1.5rem;
      background: var(--bg-surface);
      border-top: 1px solid var(--border);
      flex-shrink: 0;
    }
    .chat-input-row {
      display: flex;
      gap: 0.65rem;
      max-width: 860px;
      width: 100%;
      margin: 0 auto;
      align-items: flex-end;
    }
    .chat-input {
      flex: 1;
      background: var(--bg-page);
      border: 1px solid var(--border);
      border-radius: var(--radius-lg);
      padding: 0.65rem 0.85rem;
      color: var(--text-main);
      font-size: 0.875rem;
      font-family: inherit;
      outline: none;
      resize: none;
      min-height: 42px;
      max-height: 120px;
      line-height: 1.4;
      transition: all 0.15s ease;
    }
    .chat-input:focus {
      border-color: var(--primary);
      background: var(--bg-surface);
      box-shadow: 0 0 0 2px var(--primary-subtle);
    }

    /* Modal dialogs */
    .modal-overlay {
      position: fixed;
      top: 0; left: 0; right: 0; bottom: 0;
      background: rgba(15, 23, 42, 0.4);
      backdrop-filter: blur(2px);
      display: none;
      align-items: center;
      justify-content: center;
      z-index: 100;
    }
    .modal-box {
      background: var(--bg-surface);
      border-radius: var(--radius-xl);
      border: 1px solid var(--border);
      width: 100%;
      max-width: 580px;
      max-height: 85vh;
      overflow-y: auto;
      box-shadow: var(--shadow-lg);
      display: flex;
      flex-direction: column;
    }
    .modal-header {
      padding: 1rem 1.25rem;
      border-bottom: 1px solid var(--border);
      display: flex;
      align-items: center;
      justify-content: space-between;
    }
    .modal-title {
      font-size: 0.95rem;
      font-weight: 600;
      color: var(--text-main);
    }
    .modal-body {
      padding: 1.25rem;
      font-size: 0.85rem;
      color: var(--text-secondary);
      line-height: 1.5;
    }
    .modal-footer {
      padding: 0.75rem 1.25rem;
      border-top: 1px solid var(--border);
      background: var(--bg-page);
      display: flex;
      justify-content: flex-end;
      gap: 0.5rem;
      border-bottom-left-radius: var(--radius-xl);
      border-bottom-right-radius: var(--radius-xl);
    }

    /* Toast Notification */
    #toast-msg {
      position: fixed;
      bottom: 24px;
      right: 24px;
      background: var(--text-main);
      color: white;
      padding: 0.65rem 1rem;
      border-radius: var(--radius-md);
      font-size: 0.8rem;
      font-weight: 500;
      box-shadow: var(--shadow-lg);
      z-index: 120;
      display: none;
      align-items: center;
      gap: 0.5rem;
      animation: fadeIn 0.2s ease;
    }
    @keyframes fadeIn { from { opacity: 0; transform: translateY(6px); } to { opacity: 1; transform: translateY(0); } }

    /* Accessibility focus indicators */
    button:focus-visible, input:focus-visible, select:focus-visible, textarea:focus-visible {
      outline: 2px solid var(--border-focus);
      outline-offset: 1px;
    }

    /* Screen reader only utility */
    .sr-only {
      position: absolute;
      width: 1px;
      height: 1px;
      padding: 0;
      margin: -1px;
      overflow: hidden;
      clip: rect(0, 0, 0, 0);
      white-space: nowrap;
      border-width: 0;
    }

    /* Responsive adjustments */
    @media (max-width: 1024px) {
      .sidebar { width: 200px; }
      .list-panel { width: 320px; }
    }
    @media (max-width: 768px) {
      .sidebar { display: none; }
      .list-panel { width: 100%; }
      #viewInbox { flex-direction: column; }
    }
  </style>
</head>
<body>
  <!-- Header Bar -->
  <header>
    <div class="brand">
      <div class="brand-icon" aria-hidden="true">
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round">
          <path d="M4 4h16c1.1 0 2 .9 2 2v12c0 1.1-.9 2-2 2H4c-1.1 0-2-.9-2-2V6c0-1.1.9-2 2-2z"></path>
          <polyline points="22,6 12,13 2,6"></polyline>
        </svg>
      </div>
      <span>Mail Agent</span>
    </div>

    <!-- Segmented Mode Switcher -->
    <div class="mode-switcher" role="tablist">
      <button class="mode-btn active" id="btnModeInbox" onclick="switchView('inbox')" role="tab" aria-selected="true">
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
          <polyline points="22 12 16 12 14 15 10 15 8 12 2 12"></polyline>
          <path d="M5.45 5.11L2 12v6a2 2 0 0 0 2 2h16a2 2 0 0 0 2-2v-6l-3.45-6.89A2 2 0 0 0 16.76 4H7.24a2 2 0 0 0-1.79 1.11z"></path>
        </svg>
        <span>Smart Inbox</span>
      </button>
      <button class="mode-btn" id="btnModeChat" onclick="switchView('chat')" role="tab" aria-selected="false">
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
          <path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"></path>
        </svg>
        <span>AI Assistant</span>
      </button>
    </div>

    <!-- Header Actions -->
    <div class="header-actions">
      <button class="btn btn-secondary" id="btnSync" onclick="syncGmail()" title="Fetch recent Gmail messages (Read-Only)">
        <svg id="iconSync" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
          <polyline points="23 4 23 10 17 10"></polyline>
          <polyline points="1 20 1 14 7 14"></polyline>
          <path d="M3.51 9a9 9 0 0 1 14.85-3.36L23 10M1 14l4.64 4.36A9 9 0 0 0 20.49 15"></path>
        </svg>
        <span>Sync Gmail</span>
      </button>

      <button class="btn btn-secondary" id="btnClassify" onclick="classifyPending()" title="Execute local LLM classification for pending messages">
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
          <polygon points="13 2 3 14 12 14 11 22 21 10 12 10 13 2"></polygon>
        </svg>
        <span>Classify</span>
      </button>

      <button class="btn btn-secondary" onclick="openDigestModal()" title="View today's generated daily digest">
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
          <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"></path>
          <polyline points="14 2 14 8 20 8"></polyline>
          <line x1="16" y1="13" x2="8" y2="13"></line>
          <line x1="16" y1="17" x2="8" y2="17"></line>
          <polyline points="10 9 9 9 8 9"></polyline>
        </svg>
        <span>Digest</span>
      </button>

      <button class="btn btn-icon" onclick="openShortcutsModal()" title="Keyboard shortcuts (?)">
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
          <circle cx="12" cy="12" r="10"></circle>
          <path d="M9.09 9a3 3 0 0 1 5.83 1c0 2-3 3-3 3"></path>
          <line x1="12" y1="17" x2="12.01" y2="17"></line>
        </svg>
      </button>
    </div>
  </header>

  <div class="app-body">
    <!-- Left Navigation Sidebar -->
    <aside class="sidebar">
      <div class="nav-section">
        <div class="nav-label">Priorities</div>
        <div class="nav-item active" id="navItemNeedsAction" data-filter="needs_action" onclick="setFilter('needs_action', this)">
          <div class="nav-item-left">
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
              <circle cx="12" cy="12" r="10"></circle>
              <line x1="12" y1="8" x2="12" y2="12"></line>
              <line x1="12" y1="16" x2="12.01" y2="16"></line>
            </svg>
            <span>Needs Attention</span>
            <span class="sr-only">Needs Action</span>
          </div>
          <span class="badge" id="badgeNeedsAction">0</span>
        </div>

        <div class="nav-item" data-filter="interviews" onclick="setFilter('interviews', this)">
          <div class="nav-item-left">
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
              <path d="M16 21v-2a4 4 0 0 0-4-4H5a4 4 0 0 0-4 4v2"></path>
              <circle cx="8.5" cy="7" r="4"></circle>
              <polyline points="17 11 19 13 23 9"></polyline>
            </svg>
            <span>Interviews</span>
          </div>
          <span class="badge" id="badgeInterviews">0</span>
        </div>

        <div class="nav-item" data-filter="deadlines" onclick="setFilter('deadlines', this)">
          <div class="nav-item-left">
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
              <circle cx="12" cy="12" r="10"></circle>
              <polyline points="12 6 12 12 16 14"></polyline>
            </svg>
            <span>Deadlines</span>
          </div>
          <span class="badge" id="badgeDeadlines">0</span>
        </div>

        <div class="nav-item" data-filter="opportunities" onclick="setFilter('opportunities', this)">
          <div class="nav-item-left">
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
              <circle cx="12" cy="8" r="7"></circle>
              <polyline points="8.21 13.89 7 23 12 20 17 23 15.79 13.88"></polyline>
            </svg>
            <span>Opportunities</span>
          </div>
          <span class="badge" id="badgeOpportunities">0</span>
        </div>
      </div>

      <div class="nav-section">
        <div class="nav-label">Review & Safety</div>
        <div class="nav-item" data-filter="review_queue" onclick="setFilter('review_queue', this)">
          <div class="nav-item-left">
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
              <path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z"></path>
            </svg>
            <span>Review Queue</span>
          </div>
          <span class="badge" id="badgeReviewQueue">0</span>
        </div>

        <div class="nav-item" data-filter="suppressed" onclick="setFilter('suppressed', this)">
          <div class="nav-item-left">
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
              <circle cx="12" cy="12" r="10"></circle>
              <line x1="4.93" y1="4.93" x2="19.07" y2="19.07"></line>
            </svg>
            <span>Promotions / Spam</span>
          </div>
          <span class="badge" id="badgeSuppressed">0</span>
        </div>
      </div>

      <div class="nav-section">
        <div class="nav-label">Archive</div>
        <div class="nav-item" data-filter="handled" onclick="setFilter('handled', this)">
          <div class="nav-item-left">
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
              <path d="M22 11.08V12a10 10 0 1 1-5.93-9.14"></path>
              <polyline points="22 4 12 14.01 9 11.01"></polyline>
            </svg>
            <span>Handled</span>
          </div>
          <span class="badge" id="badgeHandled">0</span>
        </div>

        <div class="nav-item" data-filter="all" onclick="setFilter('all', this)">
          <div class="nav-item-left">
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
              <path d="M22 19a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h5l2 3h9a2 2 0 0 1 2 2z"></path>
            </svg>
            <span>All Mail</span>
          </div>
          <span class="badge" id="badgeAll">0</span>
        </div>
      </div>

      <!-- Sidebar Status & Diagnostic Footer -->
      <div class="sidebar-footer">
        <div class="status-indicator-row" id="sidebarMonitorRow">
          <div class="status-dot" id="sidebarMonitorDot"></div>
          <span id="sidebarMonitorText">Monitor: Active (Hourly)</span>
        </div>
        <div style="font-size:0.7rem;color:var(--text-faint);" id="sidebarSyncMeta">Last: None • Next: Hourly</div>
        <button class="btn btn-secondary" onclick="openStatusModal()" style="margin-top:0.35rem;padding:0.25rem 0.5rem;font-size:0.7rem;width:100%;justify-content:center;">
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" style="width:12px;height:12px;">
            <circle cx="12" cy="12" r="3"></circle>
            <path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 0 1 0 2.83 2 2 0 0 1-2.83 0l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 0 1-2 2 2 2 0 0 1-2-2v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 0 1-2.83 0 2 2 0 0 1 0-2.83l.06-.06a1.65 1.65 0 0 0 .33-1.82 1.65 1.65 0 0 0-1.51-1H3a2 2 0 0 1-2-2 2 2 0 0 1 2-2h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 0 1 0-2.83 2 2 0 0 1 2.83 0l.06.06a1.65 1.65 0 0 0 1.82.33H9a1.65 1.65 0 0 0 1-1.51V3a2 2 0 0 1 2-2 2 2 0 0 1 2 2v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 0 1 2.83 0 2 2 0 0 1 0 2.83l-.06.06a1.65 1.65 0 0 0-.33 1.82V9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 0 1 2 2 2 2 0 0 1-2 2h-.09a1.65 1.65 0 0 0-1.51 1z"></path>
          </svg>
          <span>Service Health & Info</span>
        </button>
      </div>
    </aside>

    <!-- VIEW 1: SMART INBOX -->
    <div id="viewInbox" style="display:flex;flex:1;overflow:hidden;">
      <!-- Email List Column -->
      <section class="list-panel">
        <div class="list-header">
          <div class="search-box">
            <span class="search-icon">
              <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                <circle cx="11" cy="11" r="8"></circle>
                <line x1="21" y1="21" x2="16.65" y2="16.65"></line>
              </svg>
            </span>
            <input type="text" class="search-input" id="searchInput" placeholder="Search sender, subject, summary... (/)" aria-label="Search emails">
            <span class="search-clear" id="searchClear" onclick="clearSearch()">&times;</span>
          </div>
          <div class="list-controls">
            <span id="listHeading" style="font-weight:600;color:var(--text-main);">Needs Attention</span>
            <div style="display:flex;align-items:center;gap:0.4rem;">
              <select class="list-sort-select" id="sortSelect" onchange="renderList()">
                <option value="newest">Newest first</option>
                <option value="priority">Priority / Urgency</option>
                <option value="deadline">Nearest deadline</option>
              </select>
              <span id="listCount">0 items</span>
            </div>
          </div>
        </div>

        <div class="email-scroll" id="emailListContainer">
          <!-- Rendered email cards -->
        </div>
      </section>

      <!-- Email Reading & Analysis Panel -->
      <section class="detail-panel" id="detailPanel">
        <!-- Empty State -->
        <div class="detail-empty" id="emptyDetailMsg">
          <div class="detail-empty-icon">
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
              <path d="M4 4h16c1.1 0 2 .9 2 2v12c0 1.1-.9 2-2 2H4c-1.1 0-2-.9-2-2V6c0-1.1.9-2 2-2z"></path>
              <polyline points="22,6 12,13 2,6"></polyline>
            </svg>
          </div>
          <div style="font-weight:600;color:var(--text-main);">Select a message to review</div>
          <div style="font-size:0.8rem;color:var(--text-muted);">Inspect detected intent, deadlines, AI recommended actions, and conversation evidence.</div>
        </div>

        <!-- Populated Detail Content -->
        <div class="detail-container" id="detailContent" style="display:none;">
          <!-- Detail Header Card -->
          <div class="detail-card">
            <div class="detail-header-actions">
              <div class="card-badges">
                <span class="tag" id="detailIntentTag">INTENT</span>
                <span class="tag" id="detailImportanceTag">IMPORTANCE</span>
                <span class="tag" id="detailUrgencyTag">URGENCY</span>
                <span class="tag tag-urgent" id="detailSpamTag" style="display:none;">SUPPRESSED SPAM</span>
              </div>
              <div class="detail-actions-group">
                <button class="btn btn-success" id="btnActionHandle" onclick="toggleHandled()">
                  <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round">
                    <polyline points="20 6 9 17 4 12"></polyline>
                  </svg>
                  <span>Mark Handled</span>
                </button>
                <button class="btn btn-secondary" onclick="askAiAboutSelected()">
                  <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                    <path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"></path>
                  </svg>
                  <span>Ask Assistant</span>
                </button>
                <button class="btn btn-secondary" id="btnActionSpam" onclick="toggleSpam()">
                  <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                    <circle cx="12" cy="12" r="10"></circle>
                    <line x1="4.93" y1="4.93" x2="19.07" y2="19.07"></line>
                  </svg>
                  <span>Suppress Spam</span>
                </button>
                <button class="btn btn-secondary" id="btnActionRestore" onclick="restoreEmail()" style="display:none;">
                  <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                    <polyline points="1 4 1 10 7 10"></polyline>
                    <path d="M3.51 15a9 9 0 1 0 2.13-9.36L1 10"></path>
                  </svg>
                  <span>Restore</span>
                </button>
                <button class="btn btn-secondary" id="btnActionReanalyze" onclick="reanalyzeCurrentEmail()" title="Update AI summary & actions">
                  <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                    <polygon points="13 2 3 14 12 14 11 22 21 10 12 10 13 2"></polygon>
                  </svg>
                  <span>Re-analyze</span>
                </button>
              </div>
            </div>

            <h1 class="detail-subject" id="detailSubject">Subject Line</h1>

            <div class="detail-meta-grid">
              <div class="detail-meta-item">From: <strong id="detailSender">sender</strong></div>
              <div class="detail-meta-item">Date: <span id="detailDate">date</span></div>
              <div class="detail-meta-item" id="detailStatusBadge" style="font-weight:500;"></div>
              <code id="detailId" style="display:none;">id</code>
            </div>
          </div>

          <!-- Imminent Deadline Alert Banner -->
          <div class="deadline-alert" id="detailDeadlineAlert" style="display:none;">
            <div class="deadline-alert-icon">
              <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round">
                <circle cx="12" cy="12" r="10"></circle>
                <polyline points="12 6 12 12 16 14"></polyline>
              </svg>
            </div>
            <div>
              <div style="font-weight:600;color:var(--urgent-text);font-size:0.875rem;">Approaching Action Deadline</div>
              <div style="font-size:0.8rem;color:var(--text-secondary);" id="detailDeadlineText">Due on...</div>
            </div>
          </div>

          <!-- Action Required Banner (Dynamic for actionable emails) -->
          <div id="detailActionRequiredBanner" class="action-required-banner" style="display:none;">
            <div class="action-required-left">
              <div class="action-required-icon">
                <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round" style="width:18px;height:18px;">
                  <polyline points="9 11 12 14 22 4"></polyline>
                  <path d="M21 12v7a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h11"></path>
                </svg>
              </div>
              <div>
                <div style="font-weight:700;color:#1E1B4B;font-size:0.875rem;text-transform:uppercase;letter-spacing:0.04em;">Action Required</div>
                <div style="font-size:0.85rem;color:#312E81;margin-top:0.15rem;font-weight:500;" id="detailActionBannerText">Please take required action.</div>
              </div>
            </div>
            <button class="btn btn-primary" onclick="draftReplyForSelected()" style="font-size:0.75rem;padding:0.35rem 0.8rem;white-space:nowrap;">Draft Action Response ➔</button>
          </div>

          <!-- Resume Match Badge (For matched Naukri/LinkedIn/Unstop roles) -->
          <div id="detailResumeMatchBanner" class="resume-match-banner" style="display:none;">
            <span style="font-weight:600;">🎯 Resume Skills Match:</span>
            <span id="detailResumeMatchSkills" style="font-weight:500;color:#047857;">Python • AI/ML</span>
          </div>

          <!-- AI Intelligence Analysis Card -->
          <div class="ai-card">
            <div class="ai-card-title">
              <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round">
                <polygon points="13 2 3 14 12 14 11 22 21 10 12 10 13 2"></polygon>
              </svg>
              <span id="detailBriefTitle">Summary & Insights</span>
            </div>

            <p style="font-size:0.875rem;color:var(--text-main);line-height:1.55;" id="detailSummaryText">Summary...</p>

            <div class="recommendation-box" id="detailRecommendationBox">
              <div class="recommendation-title">
                <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round" style="width:13px;height:13px;">
                  <polyline points="9 11 12 14 22 4"></polyline>
                  <path d="M21 12v7a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h11"></path>
                </svg>
                <span>Suggested next step</span>
              </div>
              <div class="recommendation-text" id="detailActionText">Action item...</div>
            </div>

            <div class="intel-grid" style="margin-top:1rem;">
              <div class="intel-cell">
                <div class="intel-label">Email type</div>
                <div class="intel-value" id="detailIntentText">Interview</div>
              </div>
              <div class="intel-cell">
                <div class="intel-label">Importance</div>
                <div class="intel-value" id="detailImportanceText">High</div>
              </div>
              <div class="intel-cell">
                <div class="intel-label">Urgency</div>
                <div class="intel-value" id="detailUrgencyText">Urgent</div>
              </div>
            </div>

            <!-- Evidence Quotes -->
            <div style="margin-top:0.75rem;">
              <div class="intel-label" style="margin-bottom:0.35rem;">Supporting details</div>
              <div id="detailEvidenceList">
                <!-- Quoted evidence strings -->
              </div>
              <div style="font-size:0.72rem;color:var(--text-muted);margin-top:0.35rem;" id="detailPriorityReason">Reason...</div>
            </div>
          </div>

          <!-- Attachments Section -->
          <div id="detailAttachmentsSection" style="display:none;" class="detail-card">
            <div class="ai-card-title">
              <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                <path d="M21.44 11.05l-9.19 9.19a6 6 0 0 1-8.49-8.49l9.19-9.19a4 4 0 0 1 5.66 5.66l-9.2 9.19a2 2 0 0 1-2.83-2.83l8.49-8.48"></path>
              </svg>
              <span>Attachments (<span id="detailAttachmentsCount">0</span>)</span>
            </div>
            <div id="detailAttachmentsList" style="display:flex;flex-direction:column;gap:0.4rem;"></div>
          </div>

          <!-- Conversation Thread History -->
          <div id="detailThreadSection" style="display:none;" class="detail-card">
            <div class="ai-card-title">
              <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                <line x1="8" y1="6" x2="21" y2="6"></line>
                <line x1="8" y1="12" x2="21" y2="12"></line>
                <line x1="8" y1="18" x2="21" y2="18"></line>
                <line x1="3" y1="6" x2="3.01" y2="6"></line>
                <line x1="3" y1="12" x2="3.01" y2="12"></line>
                <line x1="3" y1="18" x2="3.01" y2="18"></line>
              </svg>
              <span>Conversation Thread (<span id="detailThreadCount">0</span> messages)</span>
            </div>
            <div id="detailThreadList" style="display:flex;flex-direction:column;gap:0.4rem;max-height:220px;overflow-y:auto;"></div>
          </div>

          <!-- Full Email Message Body (Collapsible for Clean Reading) -->
          <div class="email-body-card" style="margin-top:1rem;">
            <details id="detailBodyAccordion" style="cursor:pointer;" open>
              <summary style="display:flex;justify-content:space-between;align-items:center;list-style:none;outline:none;user-select:none;">
                <span class="ai-card-title" style="margin-bottom:0;display:flex;align-items:center;gap:0.4rem;">
                  <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" style="width:14px;height:14px;">
                    <path d="M4 4h16c1.1 0 2 .9 2 2v12c0 1.1-.9 2-2 2H4c-1.1 0-2-.9-2-2V6c0-1.1.9-2 2-2z"></path>
                    <polyline points="22,6 12,13 2,6"></polyline>
                  </svg>
                  <span id="detailBodyTitleText">Full Message Text</span>
                </span>
                <div style="display:flex;gap:0.4rem;align-items:center;">
                  <span id="detailBodyToggleHint" style="font-size:0.7rem;color:var(--text-muted);margin-right:0.35rem;">[Click to collapse / expand]</span>
                  <button class="btn btn-secondary" onclick="event.preventDefault(); toggleRawPreview();" style="font-size:0.7rem;padding:0.2rem 0.45rem;" id="btnToggleRaw">Diagnostics</button>
                  <button class="btn btn-secondary" onclick="event.preventDefault(); draftReplyForSelected();" style="font-size:0.7rem;padding:0.2rem 0.45rem;">Draft Reply with AI</button>
                </div>
              </summary>
              <div class="email-body-text" id="detailBodyPreview" style="margin-top:0.85rem;max-height:420px;overflow-y:auto;">Message body...</div>
              <div id="detailDiagText" style="display:none;margin-top:0.75rem;padding:0.75rem;background:var(--bg-subtle);border-radius:var(--radius-md);font-family:'JetBrains Mono',monospace;font-size:0.72rem;color:var(--text-muted);"></div>
            </details>
          </div>
        </div>
      </section>
    </div>

    <!-- VIEW 2: CONVERSATIONAL AI ASSISTANT -->
    <div id="viewChat" class="chat-view" style="display:none;">
      <div class="chat-header">
        <div>
          <div style="font-weight:600;font-size:0.95rem;color:var(--text-main);">AI Assistant</div>
          <div style="font-size:0.75rem;color:var(--text-muted);">Your personal email assistant</div>
        </div>
        <button class="btn btn-secondary" onclick="clearChat()">
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
            <polyline points="3 6 5 6 21 6"></polyline>
            <path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"></path>
          </svg>
          <span>Reset Chat</span>
        </button>
      </div>

      <!-- Active Selected Email Context Banner -->
      <div class="chat-context-bar" id="chatContextBanner" style="display:none;">
        <div style="display:flex;align-items:center;gap:0.5rem;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;">
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" style="width:14px;height:14px;flex-shrink:0;">
            <path d="M4 4h16c1.1 0 2 .9 2 2v12c0 1.1-.9 2-2 2H4c-1.1 0-2-.9-2-2V6c0-1.1.9-2 2-2z"></path>
            <polyline points="22,6 12,13 2,6"></polyline>
          </svg>
          <span>Context: <strong id="chatContextTitle">No email selected</strong></span>
        </div>
        <button class="btn btn-secondary" onclick="clearChatContext()" style="font-size:0.68rem;padding:0.2rem 0.5rem;background:white;">Clear Context</button>
      </div>

      <!-- Chat Messages Scroll -->
      <div class="chat-messages" id="chatMessagesContainer">
        <div class="chat-msg assistant">
          <div class="chat-bubble">
            <strong>How can I help with your emails?</strong>
            <p style="margin-top:0.35rem;color:var(--text-secondary);">Ask about an opportunity, summarize a message, find a deadline, or draft a reply.</p>
          </div>
        </div>
      </div>

      <!-- Prompt Chips & Input Composer -->
      <div class="chat-input-bar">
        <div class="chat-prompt-chips" id="chatChipsContainer">
          <!-- Dynamic Chips injected via JS based on selected email context -->
        </div>

        <div class="chat-input-row">
          <textarea class="chat-input" id="chatInput" placeholder="Ask about an opportunity, deadline, or draft a reply... (Enter to send)" rows="1" onkeydown="handleChatKey(event)"></textarea>
          <button class="btn btn-primary" id="btnSendChat" onclick="sendChatMessage()" style="padding:0.65rem 1rem;">
            <span>Send</span>
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round">
              <line x1="22" y1="2" x2="11" y2="13"></line>
              <polygon points="22 2 15 22 11 13 2 9 22 2"></polygon>
            </svg>
          </button>
        </div>
        <div style="max-width:860px;margin:0.35rem auto 0 auto;font-size:0.68rem;color:var(--text-faint);display:flex;justify-content:space-between;">
          <span>Enter to send • Shift+Enter for new line</span>
          <span>Private & runs locally on your device</span>
        </div>
      </div>
    </div>
  </div>

  <!-- Toast Notification -->
  <div id="toast-msg">
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round" style="width:16px;height:16px;">
      <polyline points="20 6 9 17 4 12"></polyline>
    </svg>
    <span id="toast-text">Notification</span>
  </div>

  <!-- Daily Digest Modal -->
  <div class="modal-overlay" id="modalDigest">
    <div class="modal-box">
      <div class="modal-header">
        <div class="modal-title">Mail Agent — Daily Digest</div>
        <button class="btn-icon" onclick="closeDigestModal()">&times;</button>
      </div>
      <div class="modal-body">
        <pre id="digestContent" style="white-space:pre-wrap;font-family:'Inter',sans-serif;font-size:0.85rem;line-height:1.55;color:var(--text-main);"></pre>
      </div>
      <div class="modal-footer">
        <button class="btn btn-secondary" onclick="copyDigestText()">Copy Text</button>
        <button class="btn btn-primary" onclick="closeDigestModal()">Done</button>
      </div>
    </div>
  </div>

  <!-- Service Health & Diagnostics Modal -->
  <div class="modal-overlay" id="modalStatus">
    <div class="modal-box">
      <div class="modal-header">
        <div class="modal-title">Service Health & System Status</div>
        <button class="btn-icon" onclick="closeStatusModal()">&times;</button>
      </div>
      <div class="modal-body" style="display:flex;flex-direction:column;gap:0.75rem;">
        <div style="display:grid;grid-template-columns:1fr 1fr;gap:0.75rem;">
          <div class="intel-cell">
            <div class="intel-label">Local Model Inference</div>
            <div class="intel-value" style="display:flex;align-items:center;gap:0.4rem;">
              <span class="status-indicator"></span>
              <span>Qwen2.5 7B • Ollama</span>
            </div>
          </div>
          <div class="intel-cell">
            <div class="intel-label">Background Monitor</div>
            <div class="intel-value" id="diagMonitorStatus">Active (Hourly)</div>
          </div>
          <div class="intel-cell">
            <div class="intel-label">SQLite Database</div>
            <div class="intel-value" id="diagDatabase">mail_agent.db</div>
          </div>
          <div class="intel-cell">
            <div class="intel-label">Gmail OAuth Scope</div>
            <div class="intel-value">Read-Only (gmail.readonly)</div>
          </div>
        </div>

        <div style="background:var(--bg-subtle);padding:0.75rem;border-radius:var(--radius-md);font-size:0.8rem;line-height:1.5;">
          <div><strong>Last Sync:</strong> <span id="diagLastSync">None</span></div>
          <div><strong>Next Scheduled Sync:</strong> <span id="diagNextSync">In 60m</span></div>
          <div><strong>Last Heartbeat:</strong> <span id="diagHeartbeat">None</span></div>
          <div id="diagErrorRow" style="color:var(--urgent-text);margin-top:0.35rem;display:none;">
            <strong>Recent Error:</strong> <span id="diagError">None</span>
          </div>
        </div>

        <div style="border-top:1px solid var(--border);padding-top:0.75rem;display:flex;flex-direction:column;gap:0.35rem;">
          <label style="display:flex;align-items:center;gap:0.55rem;font-size:0.8rem;color:var(--text-main);cursor:pointer;font-weight:500;">
            <input type="checkbox" id="chkAutoMarkRead" onchange="toggleAutoMarkRead(this.checked)" style="width:15px;height:15px;cursor:pointer;">
            <span>Automatically mark Gmail messages as read after successful processing</span>
          </label>
          <div id="diagReadSyncNotice" style="font-size:0.72rem;color:var(--text-muted);padding-left:1.5rem;line-height:1.4;">
            Default: Disabled. Requires explicit user authorization and <code>gmail.modify</code> permission to alter Gmail labels.
          </div>
        </div>

        <div style="font-size:0.75rem;color:var(--text-muted);background:var(--bg-subtle);padding:0.5rem 0.75rem;border-radius:var(--radius-sm);border:1px dashed var(--border-strong);">
          🔒 <strong>Privacy Guarantee:</strong> All email classification, summarization, and vector retrieval run strictly locally on your machine via SQLite and Ollama. OAuth credentials and tokens are never broadcasted or logged.
        </div>
      </div>
      <div class="modal-footer">
        <button class="btn btn-primary" onclick="closeStatusModal()">Close</button>
      </div>
    </div>
  </div>

  <!-- Keyboard Shortcuts Modal -->
  <div class="modal-overlay" id="modalShortcuts">
    <div class="modal-box" style="max-width:440px;">
      <div class="modal-header">
        <div class="modal-title">Keyboard Shortcuts</div>
        <button class="btn-icon" onclick="closeShortcutsModal()">&times;</button>
      </div>
      <div class="modal-body">
        <div style="display:flex;flex-direction:column;gap:0.6rem;font-size:0.825rem;">
          <div style="display:flex;justify-content:space-between;"><span>Select next message</span><kbd class="badge">j / ↓</kbd></div>
          <div style="display:flex;justify-content:space-between;"><span>Select previous message</span><kbd class="badge">k / ↑</kbd></div>
          <div style="display:flex;justify-content:space-between;"><span>Toggle Handled</span><kbd class="badge">e</kbd></div>
          <div style="display:flex;justify-content:space-between;"><span>Suppress as Spam</span><kbd class="badge">s</kbd></div>
          <div style="display:flex;justify-content:space-between;"><span>Ask AI Assistant</span><kbd class="badge">a</kbd></div>
          <div style="display:flex;justify-content:space-between;"><span>Focus Search input</span><kbd class="badge">/</kbd></div>
          <div style="display:flex;justify-content:space-between;"><span>Switch to Smart Inbox</span><kbd class="badge">1</kbd></div>
          <div style="display:flex;justify-content:space-between;"><span>Switch to AI Assistant</span><kbd class="badge">2</kbd></div>
          <div style="display:flex;justify-content:space-between;"><span>Close dialog / Clear</span><kbd class="badge">Esc</kbd></div>
        </div>
      </div>
      <div class="modal-footer">
        <button class="btn btn-primary" onclick="closeShortcutsModal()">Got it</button>
      </div>
    </div>
  </div>

  <script>
    let currentFilter = 'needs_action';
    let currentView = 'inbox';
    let allEmails = [];
    let selectedEmailId = null;
    let deepLinkProcessed = false;
    let chatHistory = [];
    let currentStats = {};

    function switchView(view) {
      currentView = view;
      document.getElementById('btnModeInbox').classList.toggle('active', view === 'inbox');
      document.getElementById('btnModeChat').classList.toggle('active', view === 'chat');
      document.getElementById('viewInbox').style.display = view === 'inbox' ? 'flex' : 'none';
      document.getElementById('viewChat').style.display = view === 'chat' ? 'flex' : 'none';
      if (view === 'chat') {
        updateChatChips();
        setTimeout(() => { document.getElementById('chatInput').focus(); }, 100);
      }
    }

    async function loadData() {
      try {
        const statsRes = await fetch('/api/stats');
        currentStats = await statsRes.json();

        // Update count badges
        document.getElementById('badgeNeedsAction').innerText = currentStats.needs_action || 0;
        document.getElementById('badgeInterviews').innerText = currentStats.interviews || 0;
        document.getElementById('badgeDeadlines').innerText = currentStats.deadlines || 0;
        document.getElementById('badgeOpportunities').innerText = currentStats.opportunities || 0;
        document.getElementById('badgeReviewQueue').innerText = currentStats.review_queue || 0;
        document.getElementById('badgeSuppressed').innerText = currentStats.suppressed || 0;
        document.getElementById('badgeHandled').innerText = currentStats.handled || 0;
        document.getElementById('badgeAll').innerText = currentStats.total || 0;

        // Monitor & background sync status
        const dot = document.getElementById('sidebarMonitorDot');
        const text = document.getElementById('sidebarMonitorText');
        const meta = document.getElementById('sidebarSyncMeta');
        const st = (currentStats.worker_status || 'idle').toLowerCase();

        dot.className = 'status-dot';
        if (st === 'running' || st === 'idle') {
          dot.classList.add('idle');
          dot.style.background = '#10B981';
          text.innerText = 'Monitor: Active (Hourly)';
        } else if (st === 'syncing') {
          dot.classList.add('syncing');
          text.innerText = 'Monitor: Syncing...';
        } else if (st === 'error') {
          dot.classList.add('error');
          text.innerText = 'Monitor: Warning';
        } else {
          dot.classList.add('idle');
          text.innerText = 'Monitor: Standby';
        }

        const lastStr = currentStats.last_sync ? currentStats.last_sync.substring(11, 16) + ' UTC' : 'None';
        const nextStr = currentStats.next_sync ? currentStats.next_sync.substring(11, 16) + ' UTC' : 'In 60m';
        meta.innerText = `Last: ${lastStr} • Next: ${nextStr}`;

        // Fetch emails list
        const emailsRes = await fetch('/api/emails');
        allEmails = await emailsRes.json();

        function navigateToEmail(cleanId) {
          if (!cleanId || typeof cleanId !== 'string') return;
          cleanId = cleanId.trim();
          if (!cleanId) return;
          const target = allEmails.find(e => e.message_id === cleanId);
          if (target) {
            switchView('inbox');
            const isVisible = getFilteredEmails().some(e => e.message_id === cleanId);
            if (!isVisible) {
              let targetFilter = 'all';
              if (target.acknowledged_at) {
                targetFilter = 'handled';
              } else if (target.spam_status === 'confirmed_spam' || target.spam_status === 'suspected_spam' || target.intent === 'promotional') {
                targetFilter = 'suppressed';
              } else if (['interview_invitation', 'interview_scheduling', 'interview_confirmation'].includes(target.intent) || (target.subject || '').toLowerCase().includes('interview')) {
                targetFilter = 'interviews';
              } else if (target.deadline) {
                targetFilter = 'deadlines';
              } else if (['job', 'hackathon', 'competition'].includes(target.category) || ['job_opportunity', 'hackathon', 'competition'].includes(target.intent)) {
                targetFilter = 'opportunities';
              }
              setFilter(targetFilter);
            }
            selectedEmailId = cleanId;
            renderList();
            selectEmail(cleanId);
            setTimeout(() => {
              const cards = Array.from(document.querySelectorAll('.email-card'));
              const targetCard = cards.find(c => c.getAttribute('data-id') === cleanId);
              if (targetCard) targetCard.scrollIntoView({ block: 'nearest' });
            }, 50);
            return true;
          }
          return false;
        }
        window.navigateToEmail = navigateToEmail;

        // One-time deep-link handling on initial load (?email=<message_id>)
        if (!deepLinkProcessed) {
          deepLinkProcessed = true;
          const urlParams = new URLSearchParams(window.location.search);
          const rawEmail = urlParams.get('email');
          if (rawEmail) {
            if (!navigateToEmail(rawEmail)) {
              showToast('Email specified in link not found in local records');
            } else {
              return;
            }
          }
        }

        renderList();
      } catch (err) {
        console.error('Failed to load application data:', err);
      }
    }

    function setFilter(filter, el) {
      currentFilter = filter;
      document.querySelectorAll('.sidebar .nav-item').forEach(n => {
        if (el) {
          n.classList.remove('active');
        } else {
          n.classList.toggle('active', n.getAttribute('data-filter') === filter);
        }
      });
      if (el) el.classList.add('active');

      const titles = {
        'needs_action': 'Needs Attention',
        'interviews': 'Interviews & Screenings',
        'deadlines': 'Upcoming Deadlines',
        'opportunities': 'Genuine Opportunities',
        'review_queue': 'Review Queue (Uncertain)',
        'suppressed': 'Promotions & Suppressed Spam',
        'handled': 'Handled Archive',
        'all': 'All Stored Emails'
      };
      document.getElementById('listHeading').innerText = titles[filter] || 'Messages';
      if (currentView !== 'inbox') switchView('inbox');
      renderList();
    }

    function getFilteredEmails() {
      const q = document.getElementById('searchInput').value.toLowerCase().trim();
      const sort = document.getElementById('sortSelect').value;

      let list = allEmails.filter(m => {
        if (q) {
          const match = (m.subject || '').toLowerCase().includes(q) ||
                        (m.summary || '').toLowerCase().includes(q) ||
                        (m.sender || '').toLowerCase().includes(q);
          if (!match) return false;
        }

        const isHandled = !!m.acknowledged_at;
        const isSpam = m.spam_status === 'confirmed_spam' || m.spam_status === 'suspected_spam' || m.intent === 'promotional';
        const isInterview = ['interview_invitation', 'interview_scheduling', 'interview_confirmation'].includes(m.intent) ||
                            (m.subject || '').toLowerCase().includes('interview');
        const isActionable = (m.requires_action === 1 || m.requires_action === true) && !isSpam;

        if (currentFilter === 'needs_action') return !isHandled && !isSpam && (isActionable || m.urgency === 'urgent' || isInterview);
        if (currentFilter === 'interviews') return isInterview && !isSpam;
        if (currentFilter === 'deadlines') return !!m.deadline && !isHandled && !isSpam;
        if (currentFilter === 'opportunities') return !isSpam && (['job', 'hackathon', 'competition'].includes(m.category) || ['job_opportunity', 'hackathon', 'competition'].includes(m.intent));
        if (currentFilter === 'review_queue') return m.intent === 'uncertain' || m.spam_status === 'suspected_spam';
        if (currentFilter === 'suppressed') return isSpam;
        if (currentFilter === 'handled') return isHandled;
        return true; // 'all'
      });

      // Sorting
      if (sort === 'priority') {
        const pOrder = { 'urgent': 1, 'critical': 1, 'high': 2, 'normal': 3, 'low': 4 };
        list.sort((a, b) => {
          const pa = pOrder[a.urgency] || pOrder[a.importance] || 3;
          const pb = pOrder[b.urgency] || pOrder[b.importance] || 3;
          return pa - pb;
        });
      } else if (sort === 'deadline') {
        list.sort((a, b) => {
          if (!a.deadline) return 1;
          if (!b.deadline) return -1;
          return new Date(a.deadline) - new Date(b.deadline);
        });
      } else {
        // newest first (default)
        list.sort((a, b) => new Date(b.received_at || 0) - new Date(a.received_at || 0));
      }

      return list;
    }

    function renderList() {
      const filtered = getFilteredEmails();
      document.getElementById('listCount').innerText = `${filtered.length} items`;
      const container = document.getElementById('emailListContainer');

      if (filtered.length === 0) {
        container.innerHTML = `
          <div style="padding:3rem 1.5rem;text-align:center;color:var(--text-muted);font-size:0.825rem;">
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round" style="width:32px;height:32px;margin:0 auto 0.75rem auto;color:var(--text-faint);display:block;">
              <circle cx="11" cy="11" r="8"></circle>
              <line x1="21" y1="21" x2="16.65" y2="16.65"></line>
            </svg>
            No messages match this filter.
          </div>
        `;
        if (!selectedEmailId || !allEmails.some(e => e.message_id === selectedEmailId)) {
          document.getElementById('emptyDetailMsg').style.display = 'flex';
          document.getElementById('detailContent').style.display = 'none';
        }
        return;
      }

      container.innerHTML = filtered.map(m => {
        const isSelected = m.message_id === selectedEmailId ? 'selected' : '';
        const isUnhandled = !m.acknowledged_at && m.spam_status !== 'confirmed_spam';

        let intentTag = '';
        const intentVal = m.intent || m.category || 'other';
        if (['interview_invitation', 'interview_scheduling', 'interview_confirmation'].includes(m.intent) || (m.subject || '').toLowerCase().includes('interview')) {
          intentTag = '<span class="tag tag-interview">Interview</span>';
        } else if (['job', 'job_opportunity'].includes(intentVal) || m.category === 'job') {
          intentTag = '<span class="tag tag-opportunity">Job Opportunity</span>';
        } else if (['hackathon', 'competition'].includes(intentVal) || m.category === 'hackathon') {
          intentTag = '<span class="tag tag-opportunity">Hackathon</span>';
        } else if (intentVal === 'promotional' || m.spam_status === 'suspected_spam') {
          intentTag = '<span class="tag tag-subtle">Promotional</span>';
        } else if (intentVal === 'uncertain') {
          intentTag = '<span class="tag tag-high">Uncertain</span>';
        }

        let priorityTag = '';
        if (m.urgency === 'urgent') {
          priorityTag = '<span class="tag tag-urgent">Urgent</span>';
        } else if (m.importance === 'high' || m.urgency === 'high') {
          priorityTag = '<span class="tag tag-high">High</span>';
        }

        const deadlineTag = m.deadline ? `<span class="tag tag-deadline">⏰ ${formatDateShort(m.deadline)}</span>` : '';
        const dateStr = formatDateShort(m.received_at);
        const snippet = escapeHtml(m.summary || m.recommended_action || m.body || '');

        return `
          <div class="email-card ${isSelected} ${isUnhandled ? 'unhandled' : ''}" onclick="selectEmail('${m.message_id}')" data-id="${m.message_id}">
            <div class="card-top-row">
              <span class="card-sender" title="${escapeHtml(m.sender || '')}">${escapeHtml(formatSender(m.sender))}</span>
              <span class="card-date">${dateStr}</span>
            </div>
            <div class="card-subject">${escapeHtml(m.subject || '(No Subject)')}</div>
            <div class="card-preview">${snippet}</div>
            <div class="card-badges">
              ${intentTag}
              ${priorityTag}
              ${deadlineTag}
            </div>
          </div>
        `;
      }).join('');

      if (filtered.length > 0) {
        if (!selectedEmailId || !allEmails.some(e => e.message_id === selectedEmailId)) {
          selectEmail(filtered[0].message_id);
        } else if (filtered.some(e => e.message_id === selectedEmailId)) {
          document.querySelectorAll('.email-card').forEach(c => {
            c.classList.toggle('selected', c.getAttribute('data-id') === selectedEmailId);
          });
        }
      }
    }

    function selectEmail(id) {
      selectedEmailId = id;
      document.querySelectorAll('.email-card').forEach(c => {
        c.classList.toggle('selected', c.getAttribute('data-id') === id);
      });

      const email = allEmails.find(e => e.message_id === id);
      if (!email) return;

      document.getElementById('emptyDetailMsg').style.display = 'none';
      document.getElementById('detailContent').style.display = 'flex';

      document.getElementById('detailSubject').innerText = email.subject || '(No Subject)';
      document.getElementById('detailSender').innerText = email.sender || 'Unknown';
      document.getElementById('detailDate').innerText = email.received_at ? new Date(email.received_at).toLocaleString() : '';
      document.getElementById('detailId').innerText = email.message_id;

      // Status tags
      const urgTag = document.getElementById('detailUrgencyTag');
      urgTag.innerText = (email.urgency || 'normal').toUpperCase();
      urgTag.className = 'tag ' + (email.urgency === 'urgent' ? 'tag-urgent' : email.urgency === 'high' ? 'tag-high' : 'tag-subtle');

      const impTag = document.getElementById('detailImportanceTag');
      impTag.innerText = (email.importance || 'normal').toUpperCase();
      impTag.className = 'tag ' + (email.importance === 'high' || email.importance === 'critical' ? 'tag-high' : 'tag-subtle');

      const intentTag = document.getElementById('detailIntentTag');
      const intentVal = (email.intent || email.category || 'other').replace('_', ' ');
      intentTag.innerText = intentVal.toUpperCase();
      intentTag.className = 'tag ' + (intentVal.includes('interview') ? 'tag-interview' : ['job', 'hackathon'].some(k => intentVal.includes(k)) ? 'tag-opportunity' : 'tag-subtle');

      const isSpam = email.spam_status === 'confirmed_spam' || email.spam_status === 'suspected_spam';
      document.getElementById('detailSpamTag').style.display = isSpam ? 'inline-flex' : 'none';
      document.getElementById('btnActionRestore').style.display = isSpam ? 'inline-flex' : 'none';

      // Handled status badge & button
      const statusBadge = document.getElementById('detailStatusBadge');
      const handleBtn = document.getElementById('btnActionHandle');
      if (email.acknowledged_at) {
        statusBadge.innerHTML = `<span style="color:var(--success-text);">✓ Handled (${formatDateShort(email.acknowledged_at)})</span>`;
        handleBtn.innerHTML = `<span>↩ Mark Unhandled</span>`;
        handleBtn.className = 'btn btn-secondary';
      } else {
        statusBadge.innerHTML = `<span style="color:var(--warning-text);">● Pending Action</span>`;
        handleBtn.innerHTML = `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round" style="width:14px;height:14px;"><polyline points="20 6 9 17 4 12"></polyline></svg><span>Mark Handled</span>`;
        handleBtn.className = 'btn btn-success';
      }

      // Deadline Alert
      const deadlineAlert = document.getElementById('detailDeadlineAlert');
      if (email.deadline) {
        deadlineAlert.style.display = 'flex';
        const parsed = new Date(email.deadline);
        const deadlineDisp = !isNaN(parsed.getTime()) ? parsed.toLocaleString() : email.deadline;
        document.getElementById('detailDeadlineText').innerText = `Deadline scheduled: ${deadlineDisp}`;
      } else {
        deadlineAlert.style.display = 'none';
      }

      // Dynamic Action Required Banner
      const actionBanner = document.getElementById('detailActionRequiredBanner');
      const actionTextEl = document.getElementById('detailActionBannerText');
      const bodyAccordion = document.getElementById('detailBodyAccordion');
      const briefTitle = document.getElementById('detailBriefTitle');
      const resumeMatchBanner = document.getElementById('detailResumeMatchBanner');
      const resumeSkillsEl = document.getElementById('detailResumeMatchSkills');

      const isActionable = Boolean(email.requires_action && !email.acknowledged_at);
      if (isActionable) {
        actionBanner.style.display = 'flex';
        actionTextEl.innerText = email.recommended_action || 'Action needed: Review details and formulate response.';
        briefTitle.innerText = '⚡ Action needed & Key Summary';
        // Keep accordion open for actionable emails so user can read context
        if (bodyAccordion) bodyAccordion.open = true;
      } else {
        actionBanner.style.display = 'none';
        briefTitle.innerText = 'Summary & Insights';
        // For purely informational emails, collapse the heavy raw text by default to favor summary
        if (bodyAccordion) bodyAccordion.open = false;
      }

      // Check for Resume RAG Skills Match in Priority Reason or Category
      const reasonStr = (email.priority_reason || '') + ' ' + (email.recommended_action || '');
      const matchRegex = /matching your resume skills \(([^)]+)\)|matching your profile \(([^)]+)\)/i;
      const matchFound = reasonStr.match(matchRegex);
      if (matchFound && (matchFound[1] || matchFound[2])) {
        resumeMatchBanner.style.display = 'flex';
        resumeSkillsEl.innerText = (matchFound[1] || matchFound[2]).trim();
      } else if (['job', 'hackathon'].includes(email.category || '') && email.importance === 'high') {
        resumeMatchBanner.style.display = 'flex';
        resumeSkillsEl.innerText = 'AI/ML • Python • Agentic AI Workflows';
      } else {
        resumeMatchBanner.style.display = 'none';
      }

      // Intelligence Box
      document.getElementById('detailSummaryText').innerText = email.summary || 'No summary extracted for this message.';
      document.getElementById('detailActionText').innerText = email.recommended_action || (email.requires_action ? 'Review details and formulate response.' : 'No immediate action required.');
      document.getElementById('detailIntentText').innerText = (email.intent || email.category || 'other').replace('_', ' ');
      document.getElementById('detailImportanceText').innerText = (email.importance || 'normal').toUpperCase();
      document.getElementById('detailUrgencyText').innerText = (email.urgency || 'normal').toUpperCase();

      // Evidence list
      const evContainer = document.getElementById('detailEvidenceList');
      let evidenceList = [];
      if (email.evidence) {
        try {
          evidenceList = typeof email.evidence === 'string' ? JSON.parse(email.evidence) : email.evidence;
        } catch(e) {
          evidenceList = [email.evidence];
        }
      }
      if (Array.isArray(evidenceList) && evidenceList.length > 0) {
        evContainer.innerHTML = evidenceList.map(ev => `<div class="evidence-item">"${escapeHtml(ev)}"</div>`).join('');
      } else {
        evContainer.innerHTML = '<div style="font-size:0.75rem;color:var(--text-faint);">No direct quote evidence extracted.</div>';
      }

      document.getElementById('detailPriorityReason').innerText = email.priority_reason ? 'Why this matters: ' + email.priority_reason : '';
      document.getElementById('detailBodyPreview').innerText = email.body || '(Empty body)';
      document.getElementById('detailDiagText').innerText = `Message ID: ${email.message_id} | Status: ${email.status} | Version: ${email.analysis_version || '2.0.0'}\nRaw Source: ${email.sender} -> ${email.received_at}`;

      // Attachments rendering
      const attSection = document.getElementById('detailAttachmentsSection');
      const attList = document.getElementById('detailAttachmentsList');
      const attCount = document.getElementById('detailAttachmentsCount');
      let atts = [];
      if (email.attachments) {
        try {
          atts = typeof email.attachments === 'string' ? JSON.parse(email.attachments) : email.attachments;
        } catch(e) { atts = []; }
      }
      if (Array.isArray(atts) && atts.length > 0) {
        attSection.style.display = 'block';
        attCount.innerText = atts.length;
        attList.innerHTML = atts.map(a => `
          <div class="attachment-card">
            <div class="attachment-meta">
              <span class="attachment-icon">
                <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" style="width:16px;height:16px;">
                  <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"></path>
                  <polyline points="14 2 14 8 20 8"></polyline>
                </svg>
              </span>
              <div>
                <div style="font-size:0.825rem;font-weight:500;color:var(--text-main);">${escapeHtml(a.filename || 'attachment')}</div>
                <div style="font-size:0.7rem;color:var(--text-muted);">${a.size ? Math.round(a.size/1024) + ' KB' : ''} • ${escapeHtml(a.mime_type || '')}</div>
              </div>
            </div>
            <div style="display:flex;gap:0.4rem;">
              <button class="btn btn-secondary" onclick="explainAttachment('${escapeHtml(a.filename || 'attachment')}')" style="font-size:0.72rem;padding:0.25rem 0.5rem;">Explain with AI</button>
            </div>
          </div>
        `).join('');
      } else {
        attSection.style.display = 'none';
        attCount.innerText = '0';
        attList.innerHTML = '';
      }

      // Thread rendering
      const threadSection = document.getElementById('detailThreadSection');
      const threadList = document.getElementById('detailThreadList');
      const threadCount = document.getElementById('detailThreadCount');
      if (email.thread_id) {
        fetch(`/api/thread?thread_id=${encodeURIComponent(email.thread_id)}`)
          .then(r => r.json())
          .then(tEmails => {
            if (Array.isArray(tEmails) && tEmails.length > 1) {
              threadSection.style.display = 'block';
              threadCount.innerText = tEmails.length;
              threadList.innerHTML = tEmails.map(t => `
                <div class="thread-item ${t.message_id === id ? 'active' : ''}" onclick="selectEmail('${t.message_id}')">
                  <div style="display:flex;justify-content:space-between;font-size:0.75rem;">
                    <strong>${escapeHtml(formatSender(t.sender))}</strong>
                    <span style="color:var(--text-muted);">${formatDateShort(t.received_at)}</span>
                  </div>
                  <div style="font-size:0.75rem;color:var(--text-secondary);overflow:hidden;text-overflow:ellipsis;white-space:nowrap;margin-top:0.2rem;">${escapeHtml(t.subject || '')}</div>
                </div>
              `).join('');
            } else {
              threadSection.style.display = 'none';
            }
          })
          .catch(() => { threadSection.style.display = 'none'; });
      } else {
        threadSection.style.display = 'none';
      }

      updateChatChips();
    }

    async function toggleHandled() {
      if (!selectedEmailId) return;
      const email = allEmails.find(e => e.message_id === selectedEmailId);
      const endpoint = email && email.acknowledged_at ? '/api/unack' : '/api/ack';
      try {
        const res = await fetch(endpoint, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ message_id: selectedEmailId })
        });
        const data = await res.json();
        if (data.success) {
          showToast(email && email.acknowledged_at ? 'Marked as unhandled' : 'Marked as handled');
          await loadData();
          selectEmail(selectedEmailId);
        }
      } catch (err) {
        showToast('Operation failed');
      }
    }

    async function toggleSpam() {
      if (!selectedEmailId) return;
      if (!confirm('Suppress this message as promotional spam?')) return;
      try {
        const res = await fetch('/api/spam', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ message_id: selectedEmailId })
        });
        const data = await res.json();
        if (data.success) {
          showToast('Message suppressed');
          await loadData();
        }
      } catch (err) {
        showToast('Failed to suppress');
      }
    }

    async function restoreEmail() {
      if (!selectedEmailId) return;
      try {
        const res = await fetch('/api/restore', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ message_id: selectedEmailId })
        });
        const data = await res.json();
        if (data.success) {
          showToast('Restored to inbox');
          await loadData();
          selectEmail(selectedEmailId);
        }
      } catch (err) {
        showToast('Failed to restore');
      }
    }

    async function reanalyzeCurrentEmail() {
      if (!selectedEmailId) return;
      const btn = document.getElementById('btnActionReanalyze');
      btn.innerText = 'Analyzing...';
      btn.disabled = true;
      showToast('Updating summary & recommendations...');
      try {
        const res = await fetch('/api/reanalyze', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ message_id: selectedEmailId })
        });
        const data = await res.json();
        if (data.success) {
          showToast('Summary and recommendations updated');
          await loadData();
          selectEmail(selectedEmailId);
        } else {
          showToast('Analysis error: ' + (data.error || 'Failed'));
        }
      } catch (err) {
        showToast('Unable to complete analysis');
      } finally {
        btn.innerHTML = `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" style="width:14px;height:14px;"><polygon points="13 2 3 14 12 14 11 22 21 10 12 10 13 2"></polygon></svg><span>Re-analyze</span>`;
        btn.disabled = false;
      }
    }

    async function syncGmail() {
      const btn = document.getElementById('btnSync');
      btn.classList.add('disabled');
      const icon = document.getElementById('iconSync');
      icon.style.animation = 'spin 1s linear infinite';
      showToast('Checking Gmail for new messages...');
      try {
        const res = await fetch('/api/sync', { method: 'POST' });
        const data = await res.json();
        if (data.error) {
          showToast(data.message || data.error);
        } else {
          const inserted = data.inserted || 0;
          if (inserted > 0) {
            showToast(`Found ${inserted} new emails. Categorizing with AI...`);
            await loadData();
            // Automatically classify newly inserted emails
            try {
              const classRes = await fetch('/api/classify', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({limit: inserted + 5})
              });
              const classData = await classRes.json();
              showToast(`Sync & organization complete (${inserted} new)`);
            } catch (ce) {}
            await loadData();
          } else {
            showToast('Inbox up to date. No new emails found.');
            await loadData();
          }
        }
      } catch (err) {
        showToast('Gmail sync connection error');
      } finally {
        icon.style.animation = 'none';
        btn.classList.remove('disabled');
      }
    }

    async function classifyPending() {
      const btn = document.getElementById('btnClassify');
      btn.disabled = true;
      showToast('Organizing and categorizing emails...');
      try {
        const res = await fetch('/api/classify', { method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({limit: 10}) });
        const data = await res.json();
        showToast(`Finished categorizing ${data.completed} messages`);
        await loadData();
      } catch (err) {
        showToast('Categorization request error');
      } finally {
        btn.disabled = false;
      }
    }

    function toggleRawPreview() {
      const el = document.getElementById('detailDiagText');
      const btn = document.getElementById('btnToggleRaw');
      const isHidden = el.style.display === 'none';
      el.style.display = isHidden ? 'block' : 'none';
      btn.innerText = isHidden ? 'Hide Diagnostics' : 'View Diagnostics';
    }

    // AI Chat Workspace & Context Handling
    function updateChatChips() {
      const banner = document.getElementById('chatContextBanner');
      const title = document.getElementById('chatContextTitle');
      const chipsBox = document.getElementById('chatChipsContainer');

      const selected = allEmails.find(e => e.message_id === selectedEmailId);
      if (selected) {
        banner.style.display = 'flex';
        title.innerText = selected.subject || selected.message_id;

        chipsBox.innerHTML = `
          <div class="chip" onclick="quickAsk('Why is this important?')">Why is this important?</div>
          <div class="chip" onclick="quickAsk('Summarize this email')">Summarize this email</div>
          <div class="chip" onclick="quickAsk('What action should I take?')">What action should I take?</div>
          <div class="chip" onclick="quickAsk('Find related messages')">Find related messages</div>
          <div class="chip" onclick="quickAsk('Draft a reply')">Draft a reply</div>
          <div class="chip" onclick="quickAsk('Explain the attachment')">Explain the attachment</div>
        `;
      } else {
        banner.style.display = 'none';
        chipsBox.innerHTML = `
          <div class="chip" onclick="quickAsk('What should I focus on today?')">What should I focus on today?</div>
          <div class="chip" onclick="quickAsk('Which interviews are coming up?')">Upcoming interviews</div>
          <div class="chip" onclick="quickAsk('What deadlines are approaching?')">Approaching deadlines</div>
          <div class="chip" onclick="quickAsk('Check live Gmail updates')">Check live Gmail updates</div>
          <div class="chip" onclick="quickAsk('Which emails did you suppress as promotions?')">Suppressed promotions</div>
          <div class="chip" onclick="quickAsk('Summarize my inbox for the last 24 hours')">24h Inbox Summary</div>
        `;
      }
    }

    function clearChatContext() {
      selectedEmailId = null;
      document.querySelectorAll('.email-card').forEach(c => c.classList.remove('selected'));
      updateChatChips();
    }

    function askAiAboutSelected() {
      if (!selectedEmailId) return;
      switchView('chat');
      const email = allEmails.find(e => e.message_id === selectedEmailId);
      if (!email) return;
      const q = `Why is this email "${email.subject || email.message_id}" important, and what action should I take?`;
      document.getElementById('chatInput').value = q;
      sendChatMessage();
    }

    function draftReplyForSelected() {
      if (!selectedEmailId) return;
      switchView('chat');
      quickAsk('Draft a professional reply to this email');
    }

    function explainAttachment(name) {
      switchView('chat');
      quickAsk(`Explain the attachment "${name}" in this email`);
    }

    function quickAsk(text) {
      document.getElementById('chatInput').value = text;
      sendChatMessage();
    }

    function handleChatKey(e) {
      if (e.key === 'Enter' && !e.shiftKey) {
        e.preventDefault();
        sendChatMessage();
      }
    }

    async function sendChatMessage() {
      const input = document.getElementById('chatInput');
      const text = input.value.trim();
      if (!text) return;

      appendChatMessage('user', text);
      input.value = '';
      input.style.height = 'auto';

      const btn = document.getElementById('btnSendChat');
      btn.disabled = true;

      const loadingDiv = document.createElement('div');
      loadingDiv.className = 'chat-msg assistant';
      loadingDiv.id = 'chatLoadingBubble';
      loadingDiv.innerHTML = `
        <div class="chat-bubble" style="display:flex;align-items:center;gap:0.5rem;color:var(--text-muted);">
          <div class="status-dot syncing" style="background:var(--primary);"></div>
          <span>Thinking and checking your saved emails...</span>
        </div>
      `;
      document.getElementById('chatMessagesContainer').appendChild(loadingDiv);
      scrollToBottom();

      try {
        const payload = {
          message: text,
          history: chatHistory,
          selected_message_id: selectedEmailId,
        };
        const res = await fetch('/api/chat', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(payload)
        });
        const data = await res.json();
        const loadEl = document.getElementById('chatLoadingBubble');
        if (loadEl) loadEl.remove();

        appendChatMessage('assistant', data.reply, data.tool_used, data.evidence, data.freshness);
        chatHistory.push({ user: text, assistant: data.reply });
      } catch (err) {
        const loadEl = document.getElementById('chatLoadingBubble');
        if (loadEl) loadEl.remove();
        appendChatMessage('assistant', 'Sorry, I ran into an issue connecting to your local assistant.');
      } finally {
        btn.disabled = false;
      }
    }

    function formatChatToolName(toolName) {
      if (!toolName) return '';
      const map = {
        'search_emails': 'Searching your saved emails',
        'get_urgent_emails': 'Checking urgent emails',
        'get_deadlines': 'Looking up upcoming deadlines',
        'get_interviews': 'Reviewing upcoming interviews',
        'get_opportunities': 'Checking career opportunities',
        'summarize_email': 'Reading email details',
        'draft_reply': 'Preparing a response draft',
        'sync_gmail': 'Checking Gmail for fresh updates'
      };
      return map[toolName] || 'Searching your saved emails';
    }

    function renderAssistantMarkdown(rawText) {
      if (!rawText) return '';
      const lines = rawText.split('\n');
      let html = '';
      let inList = false;

      for (let i = 0; i < lines.length; i++) {
        let line = lines[i];
        const trimmed = line.trim();

        if (!trimmed) {
          if (inList) { html += '</ul>'; inList = false; }
          continue;
        }

        // Check for bullet list
        if (/^[-*•]\s+/.test(trimmed)) {
          if (!inList) { html += '<ul>'; inList = true; }
          const itemText = trimmed.replace(/^[-*•]\s+/, '');
          html += `<li>${formatInlineMarkdown(itemText)}</li>`;
          continue;
        }

        // Check for numbered list
        if (/^\d+\.\s+/.test(trimmed)) {
          if (!inList) { html += '<ol>'; inList = true; }
          const itemText = trimmed.replace(/^\d+\.\s+/, '');
          html += `<li>${formatInlineMarkdown(itemText)}</li>`;
          continue;
        }

        if (inList) { html += '</ul>'; inList = false; }

        // Headings
        if (trimmed.startsWith('### ')) {
          html += `<h4>${formatInlineMarkdown(trimmed.substring(4))}</h4>`;
        } else if (trimmed.startsWith('## ')) {
          html += `<h3>${formatInlineMarkdown(trimmed.substring(3))}</h3>`;
        } else if (trimmed.startsWith('# ')) {
          html += `<h3>${formatInlineMarkdown(trimmed.substring(2))}</h3>`;
        } else {
          html += `<p>${formatInlineMarkdown(line)}</p>`;
        }
      }
      if (inList) { html += '</ul>'; }
      return html;
    }

    function formatInlineMarkdown(text) {
      let escaped = escapeHtml(text);
      // Bold
      escaped = escaped.replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>');
      // Inline code
      escaped = escaped.replace(/`([^`]+)`/g, '<code>$1</code>');
      return escaped;
    }

    function appendChatMessage(sender, text, toolUsed, evidence, freshness) {
      const container = document.getElementById('chatMessagesContainer');
      const msgDiv = document.createElement('div');
      msgDiv.className = `chat-msg ${sender}`;

      let metaHtml = '';
      let cardsHtml = '';
      let draftBoxHtml = '';

      if (toolUsed && sender === 'assistant') {
        const friendlyTool = formatChatToolName(toolUsed);
        const freshnessBadge = freshness === 'freshly_verified_gmail'
          ? '<span class="freshness-badge gmail">● Live Gmail Verified</span>'
          : '<span class="freshness-badge cached">● Local Cache</span>';
        metaHtml = `<div class="chat-tool-meta"><span>${escapeHtml(friendlyTool)}</span>${freshnessBadge}</div>`;
      }

      // Check if evidence contains structured emails that can be rendered as interactive result cards
      if (Array.isArray(evidence) && evidence.length > 0 && evidence[0] && evidence[0].message_id) {
        cardsHtml = `
          <div class="chat-result-cards">
            ${evidence.slice(0, 4).map(e => `
              <div class="chat-result-card">
                <div style="font-weight:600;font-size:0.8rem;color:var(--text-main);overflow:hidden;text-overflow:ellipsis;white-space:nowrap;">${escapeHtml(e.subject || '(No Subject)')}</div>
                <div style="font-size:0.72rem;color:var(--text-muted);">${escapeHtml(formatSender(e.sender))}</div>
                <div style="font-size:0.72rem;color:var(--text-secondary);line-height:1.35;margin-top:0.2rem;">${escapeHtml((e.summary || e.recommended_action || '').substring(0, 100))}...</div>
                <div style="display:flex;justify-content:space-between;align-items:center;margin-top:0.35rem;">
                  <span class="tag tag-subtle">${escapeHtml(e.intent || e.category || 'email')}</span>
                  <button class="btn btn-secondary" onclick="viewEmailFromChat('${e.message_id}')" style="font-size:0.68rem;padding:0.2rem 0.5rem;">View in Inbox ➔</button>
                </div>
              </div>
            `).join('')}
          </div>
        `;
      }

      // If text looks like a draft reply, provide an interactive editable draft box with copy and send actions
      if (text.toLowerCase().includes('subject:') || text.toLowerCase().includes('dear ') || text.toLowerCase().includes('hi ') || text.toLowerCase().includes('hello ') || text.toLowerCase().includes('draft created')) {
        let cleanDraft = text.replace(/\*\*/g, '').trim();
        cleanDraft = cleanDraft.replace(/^```[a-zA-Z0-9_-]*\s*\r?\n?/i, '');
        cleanDraft = cleanDraft.replace(/\r?\n?```\s*$/i, '');
        cleanDraft = cleanDraft.replace(/```/g, '');
        cleanDraft = cleanDraft.replace(/^To:\s*[^\n\r]+\r?\n?/i, '');
        cleanDraft = cleanDraft.replace(/^Subject:\s*[^\n\r]+\r?\n?/i, '');
        cleanDraft = cleanDraft.trim();

        draftBoxHtml = `
          <div class="draft-reply-box">
            <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:0.4rem;">
              <span style="font-size:0.72rem;font-weight:600;text-transform:uppercase;color:var(--text-secondary);">Editable Response Draft</span>
              <div>
                <button class="btn btn-secondary" onclick="copyDraft(this)" style="font-size:0.7rem;padding:0.2rem 0.5rem;">Copy Draft</button>
                <button class="btn btn-primary" onclick="sendDraftFromChat(this)" style="font-size:0.7rem;padding:0.2rem 0.5rem;margin-left:0.35rem;">Confirm & Send</button>
              </div>
            </div>
            <textarea class="draft-textarea">${escapeHtml(cleanDraft)}</textarea>
            <div style="font-size:0.68rem;color:var(--text-muted);margin-top:0.35rem;">Click 'Confirm & Send' to review and send directly via your Gmail account.</div>
          </div>
        `;
      }

      const bodyHtml = sender === 'assistant' ? renderAssistantMarkdown(text) : `<div style="white-space:pre-wrap;">${escapeHtml(text)}</div>`;

      msgDiv.innerHTML = `
        ${metaHtml}
        <div class="chat-bubble">
          ${bodyHtml}
          ${cardsHtml}
          ${draftBoxHtml}
        </div>
      `;
      container.appendChild(msgDiv);
      scrollToBottom();
    }

    function viewEmailFromChat(id) {
      switchView('inbox');
      selectEmail(id);
    }

    function copyDraft(btn) {
      const textarea = btn.closest('.draft-reply-box').querySelector('.draft-textarea');
      navigator.clipboard.writeText(textarea.value);
      showToast('Draft copied to clipboard');
    }

    async function sendDraftFromChat(btn) {
      const box = btn.closest('.draft-reply-box');
      const textarea = box.querySelector('.draft-textarea');
      const text = textarea.value.trim();

      let to = '';
      let subject = 'Follow-up response';
      let body = text;

      const emailList = (typeof allEmails !== 'undefined' && Array.isArray(allEmails)) ? allEmails : [];
      const selected = emailList.find(e => e.message_id === selectedEmailId);
      if (selected && selected.sender) {
        to = selected.sender;
        subject = selected.subject ? (selected.subject.toLowerCase().startsWith('re:') ? selected.subject : 'Re: ' + selected.subject) : subject;
      }

      const toMatch = text.match(/To:\s*([^\n\r]+)/i);
      const subjMatch = text.match(/Subject:\s*([^\n\r]+)/i);
      if (toMatch) to = toMatch[1].trim();
      if (subjMatch) subject = subjMatch[1].trim();

      // Clean header lines and code blocks from body if present
      let cleanBody = body;
      cleanBody = cleanBody.replace(/^```[a-zA-Z0-9_-]*\s*\r?\n?/i, '');
      cleanBody = cleanBody.replace(/\r?\n?```\s*$/i, '');
      cleanBody = cleanBody.replace(/```/g, '');
      cleanBody = cleanBody.replace(/^To:\s*[^\n\r]+\r?\n?/i, '');
      cleanBody = cleanBody.replace(/^Subject:\s*[^\n\r]+\r?\n?/i, '');
      cleanBody = cleanBody.trim();

      if (!to) {
        to = prompt('Please enter the recipient email address:', '');
        if (!to) return;
      }

      const userConfirmed = confirm(`Are you sure you want to send this email via Gmail?\n\nTo: ${to}\nSubject: ${subject}\n\nThis will send the email directly from your Gmail account.`);
      if (!userConfirmed) return;

      btn.disabled = true;
      btn.innerText = 'Sending...';

      try {
        const res = await fetch('/api/send_email', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            to: to,
            subject: subject,
            body: cleanBody || body,
            thread_id: selected ? selected.thread_id : null,
            confirmed: true
          })
        });
        const data = await res.json();
        if (data.success) {
          showToast(`Email sent successfully to ${to}`);
          btn.style.display = 'none';
          const sentBadge = document.createElement('div');
          sentBadge.style = 'font-size:0.75rem;color:#16a34a;font-weight:600;margin-top:0.4rem;';
          sentBadge.innerText = `✅ Email sent to ${to} via Gmail`;
          box.appendChild(sentBadge);
        } else {
          showToast(`Send failed: ${data.error || 'Check authorization'}`);
          btn.disabled = false;
          btn.innerText = 'Confirm & Send';
        }
      } catch (err) {
        showToast('Error communicating with send service');
        btn.disabled = false;
        btn.innerText = 'Confirm & Send';
      }
    }

    function clearChat() {
      chatHistory = [];
      const container = document.getElementById('chatMessagesContainer');
      container.innerHTML = `
        <div class="chat-msg assistant">
          <div class="chat-bubble">
            <strong>How can I help with your emails?</strong>
            <p style="margin-top:0.35rem;color:var(--text-secondary);">Ask about an opportunity, summarize a message, find a deadline, or draft a reply.</p>
          </div>
        </div>
      `;
    }

    function scrollToBottom() {
      const container = document.getElementById('chatMessagesContainer');
      container.scrollTop = container.scrollHeight;
    }

    // Modal Dialog Helpers
    async function openDigestModal() {
      try {
        const res = await fetch('/api/digest');
        const text = await res.text();
        document.getElementById('digestContent').innerText = text;
        document.getElementById('modalDigest').style.display = 'flex';
      } catch(e) {
        showToast('Could not load daily digest');
      }
    }
    function closeDigestModal() { document.getElementById('modalDigest').style.display = 'none'; }
    function copyDigestText() {
      const text = document.getElementById('digestContent').innerText;
      navigator.clipboard.writeText(text);
      showToast('Digest text copied to clipboard');
    }

    function openStatusModal() {
      document.getElementById('diagMonitorStatus').innerText = currentStats.worker_status ? currentStats.worker_status.toUpperCase() : 'ACTIVE';
      document.getElementById('diagDatabase').innerText = currentStats.database || 'mail_agent.db';
      document.getElementById('diagLastSync').innerText = currentStats.last_sync || 'None';
      document.getElementById('diagNextSync').innerText = currentStats.next_sync || 'In 60m';
      document.getElementById('diagHeartbeat').innerText = currentStats.last_heartbeat || 'Recent';

      const errRow = document.getElementById('diagErrorRow');
      if (currentStats.recent_error) {
        errRow.style.display = 'block';
        document.getElementById('diagError').innerText = currentStats.recent_error;
      } else {
        errRow.style.display = 'none';
      }
      loadSettings();
      document.getElementById('modalStatus').style.display = 'flex';
    }
    function closeStatusModal() { document.getElementById('modalStatus').style.display = 'none'; }

    async function loadSettings() {
      try {
        const res = await fetch('/api/settings');
        const data = await res.json();
        const chk = document.getElementById('chkAutoMarkRead');
        if (chk) chk.checked = !!data.mark_as_read_enabled;
        const notice = document.getElementById('diagReadSyncNotice');
        if (notice) {
          if (data.oauth_modify_authorized) {
            notice.innerHTML = '<span style="color:var(--success-text);">✓ Authorized for Gmail label modification (gmail.modify)</span>';
          } else {
            notice.innerHTML = '<span>Default: Disabled. Requires explicit user authorization and <code>gmail.modify</code> permission to alter Gmail labels.</span>';
          }
        }
      } catch(e) {}
    }

    async function toggleAutoMarkRead(enabled) {
      try {
        const res = await fetch('/api/settings', {
          method: 'POST',
          headers: {'Content-Type': 'application/json'},
          body: JSON.stringify({mark_as_read_enabled: enabled})
        });
        const data = await res.json();
        showToast(enabled ? 'Mark as read enabled (requires modify scope)' : 'Mark as read disabled');
        loadSettings();
      } catch(e) {
        showToast('Failed to update setting');
      }
    }

    function openShortcutsModal() { document.getElementById('modalShortcuts').style.display = 'flex'; }
    function closeShortcutsModal() { document.getElementById('modalShortcuts').style.display = 'none'; }

    // Utilities
    function clearSearch() {
      const inp = document.getElementById('searchInput');
      inp.value = '';
      document.getElementById('searchClear').style.display = 'none';
      renderList();
      inp.focus();
    }

    function showToast(msg) {
      const t = document.getElementById('toast-msg');
      document.getElementById('toast-text').innerText = msg;
      t.style.display = 'flex';
      setTimeout(() => { t.style.display = 'none'; }, 3500);
    }

    function formatSender(sender) {
      if (!sender) return 'Unknown';
      const m = sender.match(/^"?([^"<]+)"?\s*<.*>$/);
      if (m && m[1].trim()) return m[1].trim();
      return sender.replace(/<.*>/, '').trim() || sender;
    }

    function formatDateShort(iso) {
      if (!iso) return '';
      try {
        const d = new Date(iso);
        if (isNaN(d.getTime())) {
          return (iso || '').length > 15 ? (iso || '').substring(0, 15) : iso;
        }
        const now = new Date();
        if (d.toDateString() === now.toDateString()) {
          return d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
        }
        return d.toLocaleDateString([], { month: 'short', day: 'numeric' });
      } catch(e) {
        return (iso || '').substring(0, 15);
      }
    }

    function escapeHtml(str) {
      return (str || '').replace(/[&<>"']/g, m => ({
        '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'
      })[m]);
    }

    // Keyboard Navigation
    document.addEventListener('keydown', e => {
      // Don't intercept when user is typing in input or textarea
      if (['INPUT', 'TEXTAREA', 'SELECT'].includes(e.target.tagName)) {
        if (e.key === 'Escape') {
          e.target.blur();
          closeDigestModal();
          closeStatusModal();
          closeShortcutsModal();
        }
        return;
      }

      if (e.key === '/') {
        e.preventDefault();
        document.getElementById('searchInput').focus();
      } else if (e.key === '1') {
        switchView('inbox');
      } else if (e.key === '2') {
        switchView('chat');
      } else if (e.key === '?') {
        openShortcutsModal();
      } else if (e.key === 'e') {
        toggleHandled();
      } else if (e.key === 's') {
        toggleSpam();
      } else if (e.key === 'a') {
        askAiAboutSelected();
      } else if (e.key === 'j' || e.key === 'ArrowDown') {
        e.preventDefault();
        navigateList(1);
      } else if (e.key === 'k' || e.key === 'ArrowUp') {
        e.preventDefault();
        navigateList(-1);
      } else if (e.key === 'Escape') {
        closeDigestModal();
        closeStatusModal();
        closeShortcutsModal();
      }
    });

    function navigateList(direction) {
      const filtered = getFilteredEmails();
      if (filtered.length === 0) return;
      let idx = filtered.findIndex(f => f.message_id === selectedEmailId);
      if (idx === -1) idx = 0;
      else idx = Math.max(0, Math.min(filtered.length - 1, idx + direction));
      selectEmail(filtered[idx].message_id);
      const card = document.querySelector(`.email-card[data-id="${filtered[idx].message_id}"]`);
      if (card) card.scrollIntoView({ block: 'nearest' });
    }

    const searchInput = document.getElementById('searchInput');
    searchInput.addEventListener('input', () => {
      document.getElementById('searchClear').style.display = searchInput.value ? 'block' : 'none';
      renderList();
    });

    // Auto-expanding chat textarea
    const chatInput = document.getElementById('chatInput');
    chatInput.addEventListener('input', () => {
      chatInput.style.height = 'auto';
      chatInput.style.height = Math.min(120, chatInput.scrollHeight) + 'px';
    });

    // Initial load and periodic refresh
    loadData();
    setInterval(loadData, 20000);

    // Active notification deep-link polling (when application window is already open)
    setInterval(async () => {
      try {
        const res = await fetch('/api/active_email');
        const data = await res.json();
        if (data && data.email && window.navigateToEmail) {
          window.navigateToEmail(data.email);
        }
      } catch (e) {}
    }, 1500);
  </script>
</body>
</html>
"""


class MailAgentHTTPHandler(BaseHTTPRequestHandler):
    storage: EmailStorage = EmailStorage()
    client: OllamaClient = OllamaClient()

    def log_message(self, format: str, *args: Any) -> None:
        # Suppress standard noisy console logs
        pass

    def _send_json(self, data: Any, status: int = 200) -> None:
        body = json.dumps(data).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_html(self, html: str, status: int = 200) -> None:
        body = html.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        parsed_url = urllib.parse.urlparse(self.path)
        path = parsed_url.path

        if path in ("/", "/index.html"):
            self._send_html(HTML_TEMPLATE)
            return

        if path == "/api/emails":
            with self.storage._connect() as conn:
                cols = {r["name"] for r in conn.execute("PRAGMA table_info(emails)").fetchall()}
                att_col = ", attachments" if "attachments" in cols else ""
                thread_col = ", thread_id" if "thread_id" in cols else ""
                rows = conn.execute(f"""
                    SELECT message_id, subject, sender, received_at, category, urgency,
                           summary, deadline, requires_action, notified_at, acknowledged_at,
                           status, body, intent, importance, spam_status, priority_reason,
                           recommended_action, evidence, analysis_version{thread_col}{att_col}
                    FROM emails
                    ORDER BY received_at DESC
                """).fetchall()
                data = [dict(r) for r in rows]
            self._send_json(data)
            return

        if path == "/api/service_status":
            svc = self.storage.get_service_status("background_monitor")
            if not svc:
                svc = {
                    "service_name": "background_monitor",
                    "status": "idle",
                    "last_sync_at": self.storage.get_checkpoint(),
                    "next_sync_at": None,
                    "last_error": None,
                    "heartbeat_at": None,
                }
            self._send_json(svc)
            return

        if path == "/api/navigate":
            query_params = urllib.parse.parse_qs(parsed_url.query)
            target_id = query_params.get("email", [""])[0]
            if target_id:
                MailAgentHTTPHandler.active_navigation_email = target_id
            target_url = f"http://localhost:8765/?email={urllib.parse.quote(target_id)}" if target_id else "http://localhost:8765/"
            # Ensure standalone window or browser focus
            try:
                open_desktop_window(target_url)
            except Exception:
                pass
            html = f"""<!DOCTYPE html>
<html><head><meta http-equiv="refresh" content="0; url={target_url}"></head>
<body><script>window.location.replace('{target_url}');</script></body></html>"""
            self._send_html(html)
            return

        if path == "/api/active_email":
            active_id = getattr(MailAgentHTTPHandler, "active_navigation_email", None)
            if active_id:
                MailAgentHTTPHandler.active_navigation_email = None
            self._send_json({"email": active_id})
            return

        if path == "/api/thread":
            query_params = urllib.parse.parse_qs(parsed_url.query)
            thread_id = query_params.get("thread_id", [""])[0]
            if thread_id:
                msgs = self.storage.get_thread_emails(thread_id)
                self._send_json(msgs)
            else:
                self._send_json([])
            return

        if path == "/api/attachment":
            query_params = urllib.parse.parse_qs(parsed_url.query)
            msg_id = query_params.get("message_id", [""])[0]
            if not msg_id:
                self._send_json({"error": "Missing message_id"}, status=400)
                return
            row = self.storage.get_email(msg_id)
            if not row:
                self._send_json({"error": "Email not found"}, status=404)
                return
            raw_atts = row.get("attachments")
            atts = []
            if raw_atts:
                try:
                    atts = json.loads(raw_atts) if isinstance(raw_atts, str) else raw_atts
                except Exception:
                    atts = []
            self._send_json({
                "message_id": msg_id,
                "attachments": atts,
                "read_only": True,
            })
            return

        if path == "/api/stats":
            with self.storage._connect() as conn:
                total = conn.execute("SELECT COUNT(*) as c FROM emails").fetchone()["c"]
                deadlines = conn.execute("""
                    SELECT COUNT(*) as c FROM emails
                    WHERE status = 'completed' AND deadline IS NOT NULL AND acknowledged_at IS NULL
                      AND (spam_status = 'not_spam' OR spam_status IS NULL)
                """).fetchone()["c"]
                jobs = conn.execute("""
                    SELECT COUNT(*) as c FROM emails
                    WHERE (category = 'job' OR intent = 'job_opportunity')
                      AND (spam_status = 'not_spam' OR spam_status IS NULL)
                """).fetchone()["c"]
                hackathons = conn.execute("""
                    SELECT COUNT(*) as c FROM emails
                    WHERE (category IN ('hackathon', 'competition') OR intent IN ('hackathon', 'competition'))
                      AND (spam_status = 'not_spam' OR spam_status IS NULL)
                """).fetchone()["c"]
                interviews = conn.execute("""
                    SELECT COUNT(*) as c FROM emails
                    WHERE (intent IN ('interview_invitation', 'interview_scheduling', 'interview_confirmation')
                           OR subject LIKE '%interview%' OR subject LIKE '%screening%')
                      AND (spam_status = 'not_spam' OR spam_status IS NULL)
                """).fetchone()["c"]
                handled = conn.execute("SELECT COUNT(*) as c FROM emails WHERE acknowledged_at IS NOT NULL").fetchone()["c"]
                urgent = conn.execute("""
                    SELECT COUNT(*) as c FROM emails
                    WHERE urgency IN ('urgent', 'high') AND acknowledged_at IS NULL
                      AND (spam_status = 'not_spam' OR spam_status IS NULL)
                """).fetchone()["c"]
                review_queue = conn.execute("""
                    SELECT COUNT(*) as c FROM emails
                    WHERE status = 'completed' AND (intent = 'uncertain' OR spam_status = 'suspected_spam')
                """).fetchone()["c"]
                suppressed = conn.execute("""
                    SELECT COUNT(*) as c FROM emails
                    WHERE spam_status IN ('suspected_spam', 'confirmed_spam') OR intent = 'promotional'
                """).fetchone()["c"]

                unack_important = conn.execute("""
                    SELECT COUNT(*) as c FROM emails
                    WHERE status = 'completed'
                      AND (spam_status = 'not_spam' OR spam_status IS NULL)
                      AND (requires_action = 1 OR urgency IN ('urgent', 'high') OR category IN ('job', 'hackathon', 'competition') OR importance IN ('critical', 'high'))
                      AND acknowledged_at IS NULL
                """).fetchone()["c"]

                all_important = conn.execute("""
                    SELECT COUNT(*) as c FROM emails
                    WHERE status = 'completed'
                      AND (spam_status = 'not_spam' OR spam_status IS NULL)
                      AND (requires_action = 1 OR urgency IN ('urgent', 'high') OR category IN ('job', 'hackathon', 'competition') OR importance IN ('critical', 'high'))
                """).fetchone()["c"]

            checkpoint = self.storage.get_checkpoint()
            account = getattr(self.storage, "get_checkpoint_account", lambda: None)()
            svc_info = self.storage.get_service_status("background_monitor") or {}

            self._send_json({
                "total": total,
                "needs_action": unack_important,
                "important": all_important,
                "opportunities": jobs + hackathons,
                "urgent": urgent,
                "deadlines": deadlines,
                "jobs": jobs,
                "hackathons": hackathons,
                "interviews": interviews,
                "review_queue": review_queue,
                "suppressed": suppressed,
                "handled": handled,
                "last_sync": checkpoint,
                "database": str(self.storage.db_path.name),
                "account": account or "",
                "worker_status": svc_info.get("status", "idle"),
                "next_sync": svc_info.get("next_sync_at"),
                "last_heartbeat": svc_info.get("heartbeat_at"),
                "recent_error": svc_info.get("last_error"),
                "recent_error_at": svc_info.get("last_error_at"),
            })
            return

        if path == "/api/settings":
            enabled_val = self.storage.get_setting("mark_as_read_enabled", "false")
            is_enabled = str(enabled_val or "").strip().lower() in ("true", "1", "yes")
            has_modify = False
            try:
                from gmail_client import GmailClient
                gc = GmailClient()
                has_modify = gc.has_modify_permission()
            except Exception:
                has_modify = False
            self._send_json({
                "mark_as_read_enabled": is_enabled,
                "oauth_modify_authorized": has_modify,
                "effective_scopes": [
                    "https://www.googleapis.com/auth/gmail.modify" if has_modify else "https://www.googleapis.com/auth/gmail.readonly"
                ],
            })
            return

        if path == "/api/digest":
            digest = build_daily_digest(self.storage)
            text = format_digest_text(digest)
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.send_header("Content-Length", str(len(text.encode("utf-8"))))
            self.end_headers()
            self.wfile.write(text.encode("utf-8"))
            return

        self.send_response(404)
        self.end_headers()

    def do_POST(self) -> None:
        parsed_url = urllib.parse.urlparse(self.path)
        path = parsed_url.path

        content_length = int(self.headers.get("Content-Length", 0))
        post_data = self.rfile.read(content_length) if content_length > 0 else b"{}"

        try:
            payload = json.loads(post_data.decode("utf-8"))
        except Exception:
            payload = {}

        if path == "/api/ack":
            msg_id = payload.get("message_id")
            if msg_id and self.storage.acknowledge(msg_id):
                if self.storage.get_setting("mark_as_read_enabled") == "true":
                    try:
                        from gmail_client import GmailClient
                        client = GmailClient()
                        if client.has_modify_permission():
                            if client.mark_as_read(msg_id):
                                self.storage.mark_read_sync_completed(msg_id)
                    except Exception:
                        pass
                self._send_json({"success": True})
            else:
                self._send_json({"success": False, "error": "Not found"}, status=404)
            return

        if path == "/api/unack":
            msg_id = payload.get("message_id")
            if msg_id:
                with self.storage._connect() as conn:
                    conn.execute("UPDATE emails SET acknowledged_at = NULL WHERE message_id = ?", (msg_id,))
                self._send_json({"success": True})
            else:
                self._send_json({"success": False, "error": "Not found"}, status=404)
            return

        if path == "/api/spam":
            msg_id = payload.get("message_id")
            if msg_id and self.storage.mark_spam(msg_id, status="confirmed_spam"):
                self._send_json({"success": True})
            else:
                self._send_json({"success": False, "error": "Not found"}, status=404)
            return

        if path == "/api/restore":
            msg_id = payload.get("message_id")
            if msg_id and self.storage.restore_email(msg_id):
                self._send_json({"success": True})
            else:
                self._send_json({"success": False, "error": "Not found"}, status=404)
            return

        if path == "/api/reanalyze":
            msg_id = payload.get("message_id")
            if not msg_id:
                self._send_json({"success": False, "error": "Missing message_id"}, status=400)
                return

            try:
                # 1. Reset email status and clear handled flag
                with self.storage._connect() as conn:
                    conn.execute("""
                        UPDATE emails
                        SET status = 'pending',
                            acknowledged_at = NULL,
                            error = NULL,
                            attempt_count = 0,
                            next_retry_at = NULL,
                            updated_at = ?
                        WHERE message_id = ?
                    """, (self.storage._now(), msg_id))

                row = self.storage.get_email(msg_id)
                if not row:
                    self._send_json({"success": False, "error": "Not found"}, status=404)
                    return

                # 2. Directly analyze this specific email
                from email_analyzer import analyze_email
                received_at = (
                    datetime.fromisoformat(row["received_at"])
                    if row.get("received_at")
                    else datetime.now(timezone.utc)
                )
                analysis = analyze_email(
                    subject=row.get("subject", ""),
                    body=row.get("body", ""),
                    client=self.client,
                    received_at=received_at,
                )
                self.storage.save_analysis(msg_id, analysis)
                self._send_json({"success": True, "analysis": analysis.model_dump()})
            except Exception as exc:
                logger.error("Re-analysis failed for %s: %s", msg_id, exc)
                self._send_json({"success": False, "error": str(exc)}, status=500)
            return

        if path == "/api/chat":
            user_msg = payload.get("message", "").strip()
            history = payload.get("history", [])
            selected_id = payload.get("selected_message_id") or payload.get("message_id")
            if not user_msg:
                self._send_json({"error": "Empty message"}, status=400)
                return

            response = answer_chat_query(
                query=user_msg,
                storage=self.storage,
                history=history,
                client=self.client,
                selected_message_id=selected_id,
            )
            self._send_json(response)
            return

        if path == "/api/sync":
            if not self.storage.acquire_lock("sync_lock", owner_id="desktop_manual_sync", lease_seconds=120):
                self._send_json({
                    "error": "Synchronization is already active in the background.",
                    "message": "Background worker is currently synchronizing. Please try again shortly.",
                }, status=409)
                return

            try:
                report = ingest_recent_emails(storage=self.storage, max_results=10)
                self._send_json({
                    "discovered": report.discovered,
                    "inserted": report.inserted,
                    "duplicates": report.duplicates,
                })
            except Exception as exc:
                self._send_json({"error": type(exc).__name__}, status=500)
            finally:
                self.storage.release_lock("sync_lock", owner_id="desktop_manual_sync")
            return

        if path == "/api/classify":
            try:
                batch_limit = int(payload.get("limit", 10))
                report = process_pending_emails(storage=self.storage, limit=batch_limit)
                # Automatically execute intelligent read-state synchronization if enabled
                try:
                    from scheduler import sync_gmail_read_states
                    sync_gmail_read_states(storage=self.storage)
                except Exception:
                    pass
                self._send_json({
                    "completed": report.completed,
                    "failed": report.failed,
                    "skipped": report.skipped,
                })
            except Exception as exc:
                self._send_json({"error": type(exc).__name__}, status=500)
            return
        if path == "/api/settings":
            val = bool(payload.get("mark_as_read_enabled"))
            self.storage.set_setting("mark_as_read_enabled", "true" if val else "false")
            has_modify = False
            try:
                from gmail_client import GmailClient
                gc = GmailClient()
                has_modify = gc.has_modify_permission()
            except Exception:
                has_modify = False
            self._send_json({
                "success": True,
                "mark_as_read_enabled": val,
                "oauth_modify_authorized": has_modify,
            })
        if path == "/api/send_email":
            draft_id = payload.get("draft_id")
            to_addr = payload.get("to")
            subject = payload.get("subject")
            body = payload.get("body")
            thread_id = payload.get("thread_id")
            confirmed = bool(payload.get("confirmed", False))

            if not confirmed:
                self._send_json({"success": False, "error": "Explicit user confirmation is required to send email."}, status=400)
                return

            if not to_addr or not body:
                self._send_json({"success": False, "error": "Recipient ('to') and email body are required."}, status=400)
                return

            from profile_rag import apply_draft_guardrails
            cleaned_body = apply_draft_guardrails(str(body))

            try:
                from gmail_client import GmailClient
                client = GmailClient()
                sent_res = client.send_message(
                    to=to_addr,
                    subject=subject or "No Subject",
                    body=cleaned_body or str(body),
                    thread_id=thread_id,
                    draft_id=draft_id,
                )
                self._send_json({"success": True, "sent": sent_res})
            except Exception as exc:
                self._send_json({"success": False, "error": str(exc)}, status=500)
            return

        self.send_response(404)
        self.end_headers()


def open_desktop_window(url: str) -> bool:
    """Launch URL inside a dedicated, standalone desktop application window (no address bar or tabs)."""
    import os
    import shutil
    import subprocess

    candidates = [
        shutil.which("msedge"),
        os.path.expandvars(r"%ProgramFiles(x86)%\Microsoft\Edge\Application\msedge.exe"),
        os.path.expandvars(r"%ProgramFiles%\Microsoft\Edge\Application\msedge.exe"),
        os.path.expandvars(r"%LocalAppData%\Microsoft\Edge\Application\msedge.exe"),
        shutil.which("chrome"),
        os.path.expandvars(r"%ProgramFiles%\Google\Chrome\Application\chrome.exe"),
        os.path.expandvars(r"%ProgramFiles(x86)%\Google\Chrome\Application\chrome.exe"),
        os.path.expandvars(r"%LocalAppData%\Google\Chrome\Application\chrome.exe"),
    ]
    for exe in candidates:
        if exe and os.path.isfile(exe):
            try:
                subprocess.Popen([exe, f"--app={url}", "--window-size=1360,880"])
                return True
            except Exception:
                continue
    return False


def start_app(port: int = 8765, open_browser: bool = True, as_window: bool = False, storage: EmailStorage | None = None) -> None:
    """Launch the local Mail Agent desktop web app server."""
    if storage is not None:
        MailAgentHTTPHandler.storage = storage

    server_address = ("127.0.0.1", port)
    httpd = ThreadingHTTPServer(server_address, MailAgentHTTPHandler)
    url = f"http://localhost:{port}"

    print(f"\n=======================================================")
    print(f"  Mail Agent Desktop App is running!")
    print(f"  URL: {url}")
    if as_window:
        print(f"  Mode: Standalone Native Desktop Window")
    print(f"  Press Ctrl+C to stop the desktop server.")
    print(f"=======================================================\n")

    # Launch background hourly monitor thread aligned to each clock hour (:00:00)
    monitor_stop = threading.Event()
    def _run_background_sync_loop():
        from scheduler import run_forever
        run_forever(
            poll_interval_seconds=3600,
            stop_event=monitor_stop,
            storage=MailAgentHTTPHandler.storage,
            align_to_hour=True,
            run_immediately=False,
        )
    monitor_thread = threading.Thread(target=_run_background_sync_loop, daemon=True, name="HourlyClockSync")
    monitor_thread.start()

    if open_browser:
        if as_window:
            threading.Timer(0.8, lambda: open_desktop_window(url) or webbrowser.open(url)).start()
        else:
            threading.Timer(0.8, lambda: webbrowser.open(url)).start()

    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping desktop application server...")
        monitor_stop.set()
    finally:
        httpd.server_close()


if __name__ == "__main__":
    start_app()

