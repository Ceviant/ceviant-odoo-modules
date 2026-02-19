# Odoo-Mifos Ledger Sync

> A robust, asynchronous ledger synchronization system that seamlessly integrates Mifos financial data with Odoo's accounting module using RabbitMQ message queues.

**Version:** 2.0.0  
**Category:** Accounting  
**Author:** Turog

## Table of Contents

- [Overview](#overview)
- [Architecture](#architecture)
- [Features](#features)
- [Installation](#installation)
- [Configuration](#configuration)
- [API Endpoints](#api-endpoints)
- [Message Queues](#message-queues)
- [Database Models](#database-models)
- [Sample Payloads](#sample-payloads)
- [Error Handling](#error-handling)
- [Cron Jobs](#cron-jobs)
- [Development](#development)

---

## Overview

The **Odoo-Mifos Ledger Sync** module provides a comprehensive API for handling ledger transactions and account management between Mifos (a microfinance management platform) and Odoo (an ERP system). The system uses asynchronous message queuing to ensure reliable, scalable processing of financial transactions.

### Key Benefits

- **Asynchronous Processing:** Decouples request handling from transaction processing using RabbitMQ
- **Automatic Retry Logic:** Built-in retry mechanism with exponential backoff for failed messages
- **Double-Entry Accounting:** Ensures all transactions maintain accounting balance (debits = credits)
- **Audit Trail:** Complete tracking of all transactions with timestamps and batch references
- **Scheduled Processing:** Cron jobs automatically process queued messages

---

## Architecture

### System Architecture Diagram

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                           MIFOS SYSTEM                                      │
└──────────────────────────────────┬──────────────────────────────────────────┘
                                   │
                    ┌──────────────┴──────────────┐
                    │                             │
                    ▼                             ▼
         ┌────────────────────┐       ┌────────────────────┐
         │  POST /ledger/     │       │  POST /ledger/     │
         │  transactions      │       │  accounts          │
         └─────────┬──────────┘       └────────┬───────────┘
                   │                           │
                   │  (1) HTTP Request         │  (1) HTTP Request
                   │      Payload              │      Payload
                   │                           │
                   ▼                           ▼
    ┌──────────────────────────────────────────────────┐
    │    ODOO APPLICATION (Django-like Framework)      │
    │                                                  │
    │  ┌─────────────────────────────────────────────┐ │
    │  │     JournalEntryController / Account        │ │
    │  │              Controller                     │ │
    │  │  - Validate Payload                         │ │
    │  │  - Return Response                          │ │
    │  └──────────────┬──────────────────────────────┘ │
    │                 │                                │
    │                 │ (2) Publish to Queue           │
    │                 ▼                                │
    │  ┌─────────────────────────────────────────────┐ │
    │  │     RabbitMQ Publisher                      │ │
    │  │  (rabbitmq_publisher.py)                    │ │
    │  │  - generate_batch_reference()               │ │
    │  │  - publish_message_to_rabbitmq()            │ │
    │  └────────────┬────────────────────────────────┘ │
    └───────────────┼─────────────────────────────────-┘
                    │
                    │ (3) Async Message Queue
                    │
    ┌───────────────▼─────────────────────────────────┐
    │              RABBITMQ MESSAGE BROKER            │
    │                                                 │
    │  ┌──────────────────────────────────────────┐   │
    │  │  transaction_queue                       │   │
    │  │  account_queue                           │   │
    │  │  update_journal_queue                    │   │
    │  └──────────────────────────────────────────┘   │
    │                                                 │
    │  ┌──────────────────────────────────────────┐   │
    │  │  transaction_failure_queue               │   │
    │  │  account_failure_queue                   │   │
    │  │  update_journal_failure_queue            │   │
    │  └──────────────────────────────────────────┘   │
    └───────────────┬─────────────────────────────────┘
                    │
                    │ (4) Consume Messages
                    │
    ┌───────────────▼─────────────────────────────────┐
    │    ODOO BATCH PROCESSOR (Cron Job)              │
    │   (batch_processor.py)                          │
    │                                                 │
    │  ┌──────────────────────────────────────────┐   │
    │  │  fetch_and_process_messages()            │   │
    │  │  - Consume from all queues               │   │
    │  │  - Route to appropriate handler          │   │
    │  │  - Retry logic on failure                │   │
    │  └──────────────────────────────────────────┘   │
    └────────────────┬────────────────────────────────┘
                     │
        ┌────────────┼──────────────┐
        │            │              │
        ▼            ▼              ▼
    ┌────────┐  ┌────────┐   ┌──────────┐
    │Process │  │Create  │   │  Update  │
    │Journal │  │Account │   │  Journal │
    │Entry   │  │        │   │  Entry   │
    └───┬────┘  └───┬────┘   └─────┬────┘
        │           │              │
        └───────────┼──────────────┘
                    │
                    │ (5) Execute Business Logic
                    │
    ┌───────────────▼─────────────────────────────────┐
    │         UTILITY MODULES                         │
    │  - journal_utils.py (Transaction Processing)    │
    │  - account_utils.py (Account Creation)          │
    │  - validation.py (Schema Validation)            │
    └────────────────┬────────────────────────────────┘
                     │
                     │ (6) Persist to Odoo Models
                     │
    ┌────────────────▼────────────────────────────────┐
    │           ODOO DATABASE                         │
    │                                                 │
    │  ┌─────────────────────────────────────────┐    │
    │  │  custom.journal.entry                   │    │
    │  │  custom.journal.entry.line              │    │
    │  │  custom.account.entry                   │    │
    │  │  account.move (Odoo Native)             │    │
    │  │  account.move.line (Odoo Native)        │    │
    │  └─────────────────────────────────────────┘    │
    └─────────────────────────────────────────────────┘
```

### Component Flow Diagram

```
REQUEST FLOW:
┌─────────┐     ┌───────────────────┐     ┌────────────┐     ┌───────────┐
│  MIFOS  │────▶│  Odoo Controller  │────▶│ Validation │────▶│ RabbitMQ  │
└─────────┘     └───────────────────┘     └────────────┘     └───────────┘
                         │
                         │ Return Response
                         │ with Batch ID
                         ▼
                    ┌──────────┐
                    │  MIFOS   │
                    └──────────┘

ASYNC PROCESSING FLOW:
┌──────────┐     ┌──────────────┐     ┌──────────────┐     ┌──────────┐
│RabbitMQ  │────▶│ Batch Proc.  │────▶│  Utilities   │────▶│ Odoo DB  │
│  Queue   │     │  (Cron Job)  │     │  & Validation│     └──────────┘
└──────────┘     └──────────────┘     └──────────────┘
     ▲                   │
     │                   │ On Failure
     │                   ▼
     │            ┌──────────────┐
     │            │ Retry Logic  │
     │            │   (5 times)  │
     │            └──────────────┘
     │                   │
     │          ┌────────┴────────┐
     │          │ Success         │ Failure
     │          ▼                 ▼
     │    ┌─────────┐        ┌───────────────┐
     └────│ ACK Msg │        │ Failure Queue │
          └─────────┘        └───────────────┘
```

---

## Features

### Core Features

✅ **Journal Entry Management**
- Create new journal entries with debits and credits
- Update existing journal entries
- Automatic balance validation
- Support for multiple currencies
- Transaction reference tracking

✅ **Account Management**
- Create new GL accounts
- Validate account types against Odoo
- Track account metadata
- Support multiple currencies

✅ **Asynchronous Processing**
- Message-based queue system with RabbitMQ
- Batch processing with retry logic
- Scheduled cron jobs for automatic processing
- Dead-letter queue for failed messages

✅ **Data Validation**
- JSON schema validation for all payloads
- GL account ID validation
- Currency code validation
- Balance checking (debits = credits)
- Date format validation

✅ **Error Handling & Recovery**
- Automatic retry with configurable attempts (default: 5)
- Exponential backoff strategy
- Dead-letter queues for failed messages
- Comprehensive error logging

✅ **Security**
- Role-based access control (RBAC)
- Public API endpoints with CSRF protection disabled
- User and system group permissions

---

## Installation

### Prerequisites

- Odoo 14.0+ (or compatible version)
- Python 3.7+
- RabbitMQ 3.8+
- PostgreSQL (Odoo requirement)

### Step 1: Clone the Repository

```bash
git clone https://github.com/ceviant/odoo-mifos-ledger-sync.git
cd odoo_mifos_ledger_sync
```

### Step 2: Install Dependencies

```bash
pip install -r requirements.txt
```

### Step 3: Copy Module to Odoo Addons

```bash
cp -r . /path/to/odoo/addons/ledger_sync
```

### Step 4: Install Module in Odoo

1. Navigate to Apps menu in Odoo
2. Search for "Ledger Sync"
3. Click Install

Or via command line:

```bash
./odoo-bin -c /etc/odoo/odoo.conf -i ledger_sync -d your_database
```

### Step 5: Configure RabbitMQ

Ensure RabbitMQ is running and accessible:

```bash
# Install RabbitMQ (macOS with Homebrew)
brew install rabbitmq
brew services start rabbitmq-server

# Or with Docker
docker run -d --name rabbitmq \
  -p 5672:5672 \
  -p 15672:15672 \
  rabbitmq:3-management
```

---

## Configuration

### Environment Variables

Create a `.env` file in the module root or configure via system environment:

```env
# RabbitMQ Configuration
RABBITMQ_HOST=rabbitmq
RABBITMQ_PORT=5672
RABBITMQ_VHOST=/
RABBITMQ_USERNAME=guest
RABBITMQ_PASSWORD=guest

# Odoo Configuration
ODOO_HOST=localhost
ODOO_PORT=8069
ODOO_DATABASE=odoo_db
ODOO_USERNAME=admin
ODOO_PASSWORD=admin

# Batch Processing
MAX_RETRIES=5
RETRY_DELAY=5  # seconds
BATCH_PROCESS_INTERVAL=1  # days
```

### Docker Compose Setup

```yaml
version: '3.8'

services:
  rabbitmq:
    image: rabbitmq:3-management
    environment:
      RABBITMQ_DEFAULT_USER: guest
      RABBITMQ_DEFAULT_PASS: guest
    ports:
      - "5672:5672"
      - "15672:15672"
    healthcheck:
      test: rabbitmq-diagnostics -q ping
      interval: 30s
      timeout: 10s
      retries: 5

  postgres:
    image: postgres:13
    environment:
      POSTGRES_DB: odoo_db
      POSTGRES_USER: odoo
      POSTGRES_PASSWORD: odoo
    ports:
      - "5432:5432"

  odoo:
    image: odoo:15
    depends_on:
      - postgres
      - rabbitmq
    environment:
      - HOST=postgres
      - USER=odoo
      - PASSWORD=odoo
      - RABBITMQ_HOST=rabbitmq
      - RABBITMQ_PORT=5672
    ports:
      - "8069:8069"
    volumes:
      - ./:/mnt/extra-addons/ledger_sync
```

### Cron Job Configuration

The module includes a cron job that processes messages from RabbitMQ every day at 8 PM:

```xml
<!-- data/cron_jobs.xml -->
<record id="ir_cron_process_batches" model="ir.cron">
    <field name="name">Process Batches from RabbitMQ</field>
    <field name="model_id" ref="custom_journal_entry.model_custom_journal_entry_batch_processor"/>
    <field name="state">code</field>
    <field name="code">model.run_batch_processor()</field>
    <field name="interval_number">1</field>
    <field name="interval_type">days</field>
    <field name="nextcall" eval="(DateTime.now().replace(hour=20, minute=0, second=0))"/>
    <field name="active">True</field>
</record>
```

To change the execution time, modify `nextcall` and `interval_number` as needed.

---

## API Endpoints

### 1. Create Journal Entry (Transaction)

**Endpoint:** `POST /ledger/transactions`

**Authentication:** Public (CSRF disabled)

**Description:** Submit a new journal entry for asynchronous processing.

#### Request

```http
POST /ledger/transactions HTTP/1.1
Host: odoo.example.com
Content-Type: application/json

{
  "branchId": "BRANCH-001",
  "transactionDate": "21 January 2026",
  "transactionReference": "TXN-2026-00001",
  "timeStamp": "2026-01-21T10:30:00Z",
  "comments": "Loan disbursement to customer",
  "currencyCode": "USD",
  "credits": [
    {
      "glAccountId": 12,
      "amount": 5000.00
    },
    {
      "glAccountId": 15,
      "amount": 3500.00
    }
  ],
  "debits": [
    {
      "glAccountId": 8,
      "amount": 8500.00
    }
  ]
}
```

#### Response - Success (202 Accepted)

```json
{
  "code": 202,
  "status": "success",
  "data": {
    "message": "Request has been successfully logged.",
    "responseId": "resp-482917"
  }
}
```

#### Response - Validation Error (400 Bad Request)

```json
{
  "code": 400,
  "status": "error",
  "data": {
    "message": "'credits' is a required property",
    "responseId": null
  }
}
```

#### Response - Processing Error (500 Internal Server Error)

```json
{
  "code": 500,
  "status": "error",
  "data": {
    "message": "Failed to publish to RabbitMQ",
    "responseId": null
  }
}
```

---

### 2. Update Journal Entry

**Endpoint:** `PUT /ledger/transactions`

**Authentication:** Public (CSRF disabled)

**Description:** Update an existing journal entry. The transaction must already exist.

#### Request

```http
PUT /ledger/transactions HTTP/1.1
Host: odoo.example.com
Content-Type: application/json

{
  "branchId": "BRANCH-001",
  "transactionDate": "21 January 2026",
  "transactionReference": "TXN-2026-00001",
  "timeStamp": "2026-01-21T14:45:00Z",
  "comments": "Updated: Loan disbursement correction",
  "currencyCode": "USD",
  "credits": [
    {
      "glAccountId": 12,
      "amount": 5500.00
    },
    {
      "glAccountId": 15,
      "amount": 3000.00
    }
  ],
  "debits": [
    {
      "glAccountId": 8,
      "amount": 8500.00
    }
  ]
}
```

#### Response - Success (202 Accepted)

```json
{
  "code": 202,
  "status": "success",
  "data": {
    "message": "Update request has been successfully logged.",
    "responseId": "resp-582946"
  }
}
```

---

### 3. Create Account

**Endpoint:** `POST /ledger/accounts`

**Authentication:** Public (CSRF disabled)

**Description:** Create a new GL account in Odoo.

#### Request

```http
POST /ledger/accounts HTTP/1.1
Host: odoo.example.com
Content-Type: application/json

{
  "account_id": "ACC-001",
  "account_name": "Customer Receivables",
  "account_code": "1200",
  "account_type": "asset_receivable",
  "currency": "USD",
  "status": "active"
}
```

#### Response - Success (200 OK)

```json
{
  "code": 200,
  "status": "success",
  "data": {
    "message": "Account creation request has been successfully logged.",
    "responseId": "resp-749382"
  }
}
```

#### Response - Validation Error (400 Bad Request)

```json
{
  "code": 400,
  "status": "error",
  "data": {
    "message": "'account_type' is a required property",
    "responseId": null
  }
}
```

#### Response - Duplicate Account (400 Bad Request)

```json
{
  "code": 400,
  "status": "error",
  "data": {
    "message": "Account with code '1200' already exists.",
    "responseId": null
  }
}
```

---

### 4. Get All Accounts

**Endpoint:** `GET /ledger/accounts`

**Authentication:** Public

**Description:** Retrieve all GL accounts with their details.

#### Request

```http
GET /ledger/accounts HTTP/1.1
Host: odoo.example.com
```

#### Response - Success (200 OK)

```json
{
  "code": 200,
  "status": "success",
  "data": [
    {
      "id": 8,
      "code": "1000",
      "name": "Cash and Cash Equivalents",
      "account_type": "asset_current",
      "currency_id": "USD",
      "reconcile": true
    },
    {
      "id": 12,
      "code": "1200",
      "name": "Customer Receivables",
      "account_type": "asset_receivable",
      "currency_id": "USD",
      "reconcile": true
    },
    {
      "id": 15,
      "code": "2100",
      "name": "Accounts Payable",
      "account_type": "liability_payable",
      "currency_id": "USD",
      "reconcile": true
    }
  ]
}
```

---

## Message Queues

### Queue Architecture

The system uses RabbitMQ with the following queues:

#### Processing Queues

| Queue | Purpose | Consumer |
|-------|---------|----------|
| `transaction_queue` | Journal entry creation requests | Batch Processor |
| `account_queue` | Account creation requests | Batch Processor |
| `update_journal_queue` | Journal entry update requests | Batch Processor |

#### Dead-Letter Queues (DLQ)

| Queue | Purpose |
|-------|---------|
| `transaction_failure_queue` | Failed journal entry transactions (after 5 retries) |
| `account_failure_queue` | Failed account creations (after 5 retries) |
| `update_journal_failure_queue` | Failed journal entry updates (after 5 retries) |

### Message Structure

All messages follow this structure:

```json
{
  "batch_ref": "resp-482917",
  "payload": {
    "branchId": "BRANCH-001",
    "transactionDate": "21 January 2026",
    "transactionReference": "TXN-2026-00001",
    "currencyCode": "USD",
    ...
  }
}
```

### Queue Configuration (batch_processor.py)

```python
# Durable queues persist messages across broker restarts
channel.queue_declare(queue='transaction_queue', durable=True)
channel.queue_declare(queue='account_queue', durable=True)
channel.queue_declare(queue='update_journal_queue', durable=True)

# Dead-letter queues for failed messages
channel.queue_declare(queue='transaction_failure_queue', durable=True)
channel.queue_declare(queue='account_failure_queue', durable=True)
channel.queue_declare(queue='update_journal_failure_queue', durable=True)
```

### Retry Logic

The system automatically retries failed messages with the following configuration:

```python
MAX_RETRIES = 5
RETRY_DELAY = 5  # seconds between retries
```

**Retry Flow:**

1. Message processed
2. If error occurs → Retry (delay 5 seconds)
3. Repeat up to 5 times
4. If still failing → Move to corresponding failure queue
5. Monitor failure queues for manual intervention

---

## Database Models

### 1. CustomJournalEntry Model

**Model Name:** `custom.journal.entry`

Stores custom journal entry metadata alongside Odoo's native `account.move`.

#### Fields

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `branch_id` | Text | No | Source branch identifier from Mifos |
| `transaction_date` | Date | Yes | Date of the transaction |
| `transaction_reference` | Char | Yes | Unique transaction identifier |
| `time_stamp` | Text | No | Transaction timestamp |
| `comments` | Text | No | Transaction notes/comments |
| `currency_id` | Many2one | Yes | Reference to res.currency |
| `company_id` | Many2one | Yes | Reference to res.company |
| `account_move_id` | Many2one | Yes | Link to native Odoo account.move |
| `journal_id` | Many2one | Yes | Reference to account.journal |
| `credit_ids` | One2many | No | Credit entry lines |
| `debit_ids` | One2many | No | Debit entry lines |

#### Database Table

```sql
CREATE TABLE custom_journal_entry (
  id SERIAL PRIMARY KEY,
  branch_id TEXT,
  transaction_date DATE NOT NULL,
  transaction_reference VARCHAR(255) NOT NULL UNIQUE,
  time_stamp TEXT,
  comments TEXT,
  currency_id INTEGER NOT NULL,
  company_id INTEGER NOT NULL,
  account_move_id INTEGER NOT NULL,
  journal_id INTEGER NOT NULL,
  create_date TIMESTAMP DEFAULT NOW(),
  write_date TIMESTAMP DEFAULT NOW()
);
```

---

### 2. CustomJournalEntryLine Model

**Model Name:** `custom.journal.entry.line`

Stores individual debit/credit lines for each journal entry.

#### Fields

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `journal_entry_id` | Many2one | Yes | Reference to custom.journal.entry |
| `gl_account_id` | Many2one | Yes | GL Account ID (account.account) |
| `amount` | Float | Yes | Amount (debit or credit) |
| `type` | Selection | Yes | 'credit' or 'debit' |

#### Database Table

```sql
CREATE TABLE custom_journal_entry_line (
  id SERIAL PRIMARY KEY,
  journal_entry_id INTEGER NOT NULL,
  gl_account_id INTEGER NOT NULL,
  amount NUMERIC(12,2) NOT NULL,
  type VARCHAR(10) NOT NULL CHECK (type IN ('credit', 'debit')),
  FOREIGN KEY (journal_entry_id) REFERENCES custom_journal_entry(id)
);
```

---

### 3. CustomAccountEntry Model

**Model Name:** `custom.account.entry`

Stores custom account metadata.

#### Fields

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `account_id` | Char | Yes | External account ID from Mifos |
| `account_name` | Char | Yes | Account name |
| `account_type` | Char | Yes | Odoo account type (e.g., asset_current) |
| `currency_id` | Many2one | Yes | Reference to res.currency |
| `account_code` | Char | Yes | GL account code (unique) |

#### Database Table

```sql
CREATE TABLE custom_account_entry (
  id SERIAL PRIMARY KEY,
  account_id VARCHAR(255) NOT NULL,
  account_name VARCHAR(255) NOT NULL,
  account_type VARCHAR(50) NOT NULL,
  currency_id INTEGER NOT NULL,
  account_code VARCHAR(255) NOT NULL UNIQUE,
  create_date TIMESTAMP DEFAULT NOW()
);
```

---

### 4. BatchProcessor Model

**Model Name:** `custom_journal_entry.batch_processor`

Handles message consumption and processing.

#### Methods

| Method | Purpose |
|--------|---------|
| `fetch_and_process_messages()` | Polls RabbitMQ queues and processes messages |
| `process_message()` | Routes messages to appropriate handler |
| `retry_or_move_to_failure_queue()` | Manages retry logic and DLQ routing |
| `send_notification()` | Sends processing status notifications |

---

## Sample Payloads

### Complete Transaction Example

#### Scenario: Loan Disbursement with Multiple GL Accounts

```json
{
  "branchId": "BRANCH-NAIROBI-001",
  "transactionDate": "21 January 2026",
  "transactionReference": "TXN-2026-00001",
  "timeStamp": "2026-01-21T10:30:45Z",
  "comments": "Loan disbursement to Group Account: GA-2026-001",
  "currencyCode": "KES",
  "credits": [
    {
      "glAccountId": 12,
      "amount": 50000.00
    }
  ],
  "debits": [
    {
      "glAccountId": 8,
      "amount": 50000.00
    }
  ]
}
```

#### Scenario: Multi-leg Transfer with Fees

```json
{
  "branchId": "BRANCH-KAMPALA-002",
  "transactionDate": "21 January 2026",
  "transactionReference": "TXN-2026-00002",
  "timeStamp": "2026-01-21T11:15:30Z",
  "comments": "Transfer with processing fee",
  "currencyCode": "UGX",
  "credits": [
    {
      "glAccountId": 12,
      "amount": 100000.00
    },
    {
      "glAccountId": 52,
      "amount": 5000.00
    }
  ],
  "debits": [
    {
      "glAccountId": 8,
      "amount": 105000.00
    }
  ]
}
```

#### Scenario: Journal Entry Update

```json
{
  "branchId": "BRANCH-NAIROBI-001",
  "transactionDate": "21 January 2026",
  "transactionReference": "TXN-2026-00001",
  "timeStamp": "2026-01-21T14:30:00Z",
  "comments": "Correction: Updated disbursement amount",
  "currencyCode": "KES",
  "credits": [
    {
      "glAccountId": 12,
      "amount": 55000.00
    }
  ],
  "debits": [
    {
      "glAccountId": 8,
      "amount": 55000.00
    }
  ]
}
```

#### Scenario: Account Creation

```json
{
  "account_id": "ACC-NAIROBI-001",
  "account_name": "Customer Loan Receivables - Nairobi Branch",
  "account_code": "1200-NAIROBI",
  "account_type": "asset_receivable",
  "currency": "KES",
  "status": "active"
}
```

---

### cURL Examples

#### Create Transaction

```bash
curl -X POST http://odoo.example.com/ledger/transactions \
  -H "Content-Type: application/json" \
  -d '{
    "branchId": "BRANCH-001",
    "transactionDate": "21 January 2026",
    "transactionReference": "TXN-2026-00001",
    "timeStamp": "2026-01-21T10:30:00Z",
    "comments": "Loan disbursement",
    "currencyCode": "USD",
    "credits": [{"glAccountId": 12, "amount": 5000}],
    "debits": [{"glAccountId": 8, "amount": 5000}]
  }'
```

#### Update Transaction

```bash
curl -X PUT http://odoo.example.com/ledger/transactions \
  -H "Content-Type: application/json" \
  -d '{
    "branchId": "BRANCH-001",
    "transactionDate": "21 January 2026",
    "transactionReference": "TXN-2026-00001",
    "comments": "Updated disbursement",
    "currencyCode": "USD",
    "credits": [{"glAccountId": 12, "amount": 5500}],
    "debits": [{"glAccountId": 8, "amount": 5500}]
  }'
```

#### Create Account

```bash
curl -X POST http://odoo.example.com/ledger/accounts \
  -H "Content-Type: application/json" \
  -d '{
    "account_id": "ACC-001",
    "account_name": "Customer Receivables",
    "account_code": "1200",
    "account_type": "asset_receivable",
    "currency": "USD",
    "status": "active"
  }'
```

#### Get All Accounts

```bash
curl -X GET http://odoo.example.com/ledger/accounts
```

---

## Error Handling

### Validation Errors

The system validates all incoming payloads against JSON schemas. Common validation errors include:

#### Missing Required Fields

```json
{
  "code": 400,
  "status": "error",
  "data": {
    "message": "'transactionReference' is a required property",
    "responseId": null
  }
}
```

#### Invalid Data Types

```json
{
  "code": 400,
  "status": "error",
  "data": {
    "message": "12.5 is not of type 'number' - 'glAccountId' must be a number",
    "responseId": null
  }
}
```

#### Invalid Account ID

```python
# In logs:
ERROR: One or more account IDs are invalid. Transaction will not be processed.
```

#### Unbalanced Entry

```python
# In logs:
ERROR: Debits and credits do not match. Total debits: 5000, Total credits: 4500
```

### Processing Errors

#### Duplicate Transaction

```python
# Error raised:
ValueError("Transaction with reference 'TXN-2026-00001' already exists.")

# In logs:
ERROR: Transaction with reference 'TXN-2026-00001' already exists.
```

#### Currency Not Found

```python
# In logs:
ERROR: Currency code XYZ not found.
```

#### RabbitMQ Connection Error

```python
# In logs:
ERROR: AMQP error: Connection refused
# Message is retried up to 5 times, then moved to failure queue
```

### Retry Logic Flow

```
Message Processing Attempt 1
    ↓
[Processing fails]
    ↓ (Wait 5 seconds)
Retry Attempt 2
    ↓
[Processing fails]
    ↓ (Wait 5 seconds)
Retry Attempt 3
    ↓
[Processing fails]
    ↓ (Wait 5 seconds)
Retry Attempt 4
    ↓
[Processing fails]
    ↓ (Wait 5 seconds)
Retry Attempt 5
    ↓
[Processing fails]
    ↓
Move to Failure Queue
(Manual intervention required)
```

### Monitoring & Debugging

```python
# Enable debug logging
logging.basicConfig(level=logging.DEBUG)

# Check RabbitMQ Management UI
# http://rabbitmq-host:15672
# Default credentials: guest/guest

# Check Odoo logs
tail -f /var/log/odoo/odoo.log

# Monitor failed messages
SELECT * FROM messages WHERE status = 'failed';
```

---

## Cron Jobs

### Batch Processing Cron

The module includes an automated cron job that processes messages from RabbitMQ queues.

#### Configuration

```xml
<record id="ir_cron_process_batches" model="ir.cron">
    <field name="name">Process Batches from RabbitMQ</field>
    <field name="model_id" ref="custom_journal_entry.model_custom_journal_entry_batch_processor"/>
    <field name="state">code</field>
    <field name="code">model.run_batch_processor()</field>
    <field name="interval_number">1</field>
    <field name="interval_type">days</field>
    <field name="numbercall">-1</field>
    <field name="active">True</field>
    <field name="nextcall" eval="(DateTime.now().replace(hour=20, minute=0, second=0))"/>
</record>
```

#### Customization

To change execution time, modify in `data/cron_jobs.xml`:

```xml
<!-- Run every 6 hours -->
<field name="interval_number">6</field>
<field name="interval_type">hours</field>

<!-- Run every 30 minutes -->
<field name="interval_number">30</field>
<field name="interval_type">minutes</field>

<!-- Set specific time (e.g., 2 PM) -->
<field name="nextcall" eval="(DateTime.now().replace(hour=14, minute=0, second=0))"/>
```

---

## Development

### Project Structure

```
odoo_mifos_ledger_sync/
├── __init__.py                           # Module initialization
├── __manifest__.py                       # Module metadata
├── requirements.txt                      # Python dependencies
├── README.md                             # This file
├── controllers/
│   ├── __init__.py
│   ├── account_controller.py            # Account API endpoints
│   └── journal_controller.py            # Journal API endpoints
├── models/
│   ├── __init__.py
│   ├── account_entry.py                 # Account model
│   ├── account_utils.py                 # Account creation logic
│   ├── batch_processor.py               # RabbitMQ message processor
│   ├── journal_entry.py                 # Journal entry models
│   ├── journal_utils.py                 # Journal entry logic
│   ├── rabbitmq_publisher.py            # RabbitMQ publisher
│   ├── validation.py                    # Payload validation
│   └── __pycache__/
├── security/
│   ├── __init__.py
│   └── ir.model.access.csv              # Access control list
├── views/
│   ├── __init__.py
│   └── custom_journal_entry_views.xml   # UI views
├── data/
│   ├── __init__.py
│   └── cron_jobs.xml                    # Scheduled tasks
└── __pycache__/
```

### Development Setup

```bash
# Clone repository
git clone https://github.com/yourusername/odoo-mifos-ledger-sync.git
cd odoo_mifos_ledger_sync

# Create virtual environment
python -m venv venv
source venv/bin/activate

# Install development dependencies
pip install -r requirements.txt
pip install pytest pytest-cov black pylint

# Start RabbitMQ (if using Docker)
docker run -d -p 5672:5672 -p 15672:15672 rabbitmq:3-management

# Run linting
pylint models/ controllers/

# Format code
black models/ controllers/
```

### Testing

```python
# Example test for journal entry validation
import pytest
from models.validation import validate_journal_entry

def test_valid_journal_entry():
    payload = {
        "transactionDate": "21 January 2026",
        "transactionReference": "TXN-2026-00001",
        "currencyCode": "USD",
        "credits": [{"glAccountId": 12, "amount": 5000}],
        "debits": [{"glAccountId": 8, "amount": 5000}]
    }
    assert validate_journal_entry(payload)[0] == True

def test_missing_required_field():
    payload = {
        "transactionDate": "21 January 2026",
        "currencyCode": "USD",
        "credits": [{"glAccountId": 12, "amount": 5000}],
        "debits": [{"glAccountId": 8, "amount": 5000}]
    }
    is_valid, error = validate_journal_entry(payload)
    assert is_valid == False
    assert "transactionReference" in error
```

### Common Development Tasks

#### Adding a New Queue Type

1. Update `batch_processor.py`:

```python
# Add new queue declaration
channel.queue_declare(queue='new_queue', durable=True)
channel.queue_declare(queue='new_queue_failure_queue', durable=True)

# Add routing in process_message()
elif queue_type == 'new_queue':
    success = handle_new_queue(payload)
```

2. Add handler in appropriate utils file

#### Adding a New Validation Rule

1. Update schema in `validation.py`:

```python
new_schema = {
    "type": "object",
    "properties": {
        "new_field": {"type": "string"}
    },
    "required": ["new_field"]
}
```

#### Adding a New API Endpoint

1. Create new method in controller:

```python
@http.route('/ledger/new-endpoint', type='http', auth='public', 
            methods=['POST'], csrf=False)
def new_endpoint(self):
    # Implementation
    pass
```

---

## Dependencies

| Package | Version | Purpose |
|---------|---------|---------|
| `pika` | 1.3.2 | RabbitMQ client library |
| `odoorpc` | 0.10.1 | Odoo XML-RPC client |
| `requests` | 2.25.1 | HTTP library |
| `jsonschema` | 4.23.0 | JSON schema validation |

---

## Support & Troubleshooting

### Common Issues

#### RabbitMQ Connection Refused

```
Error: [Errno 111] Connection refused
Solution: Ensure RabbitMQ is running and accessible
docker ps | grep rabbitmq
```

#### Currency Not Found

```
Error: Currency code XYZ not found
Solution: Ensure currency exists in Odoo
- Go to Settings > Currencies
- Add missing currency code
```

#### Transaction Already Exists

```
Error: Transaction with reference 'TXN-2026-00001' already exists
Solution: Use a unique transaction reference or update existing transaction
```

#### Unbalanced Journal Entry

```
Error: Debits and credits do not match
Solution: Ensure total debits = total credits in payload
```

---

## License

This project is licensed under the LGPL License. See [LICENSE](LICENSE) file for details.

---

## Contact & Support

For issues, questions, or contributions:

- **GitHub Issues:** [GitHub Issues Link]
- **Email:** support@turog.com
- **Documentation:** [Wiki Link]

---

## Version History

| Version | Date | Changes |
|---------|------|---------|
| 2.0.0 | 2026-01-21 | Async RabbitMQ processing, improved retry logic |
| 1.0.0 | 2025-XX-XX | Initial release |

---

**Last Updated:** 21 January 2026
