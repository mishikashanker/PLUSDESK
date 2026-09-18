# PulseDesk

## Team Status Intelligence Platform

PulseDesk is an async team status and standup platform that turns scattered daily updates into a clear, source-linked action digest.

## Problem

Distributed teams often spend valuable time chasing daily updates, identifying blockers, and converting informal messages into actionable tasks.

## Solution

PulseDesk collects short daily updates from team members and organizes them into:

- Completed work
- Current work
- Blockers
- Source-linked updates
- Team-level digest
- Structured task information

## Key Features

- Daily team status submission
- Centralized team updates
- Automatic blocker identification
- Source-linked blockers
- Team digest
- Persistent SQLite storage
- REST API using FastAPI
- React dashboard
- Privacy-focused design

## How It Works

Team Member  
↓  
Daily Update Form  
↓  
FastAPI Backend  
↓  
SQLite Database  
↓  
AI Processing  
↓  
Verified Team Digest  
↓  
Team Dashboard / Task Tracker

## Enterprise Principles

### Data Provenance

Every blocker or insight can be traced back to the original team update.

### Privacy

PulseDesk processes only information intentionally submitted through the status-update system.

It does not monitor:

- Private messages
- Typing activity
- Employee activity
- Unrelated conversations
- Personal productivity behavior

### AI Reliability

AI-generated insights are not treated as the source of truth.

The original update remains available so that team members can verify where an insight came from.

### Data Minimization

Only information required for team coordination is processed.

## Tech Stack

### Frontend

- React
- Vite
- JavaScript
- CSS

### Backend

- Python
- FastAPI
- Pydantic
- SQLite

### Future AI Layer

- LLM-based summarization
- Structured information extraction
- Source-linked insights

## Project Structure

```text
PLUSDESK/
│
├── frontend/
│   └── React + Vite application
│
├── main.py
├── requirements.txt
├── pulsedesk.db
└── README.md