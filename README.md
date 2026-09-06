# Manthrabin Developer Setup Guide

## Project Overview

Manthrabin is a Django-based backend service that provides document management and conversation capabilities with AI integration. The project uses Django REST Framework for API endpoints and Elasticsearch for document search and storage.

## Prerequisites

- Python 3.12 (used by the Docker image)
- MySQL/MariaDB
- Elasticsearch
- OpenAI API key
- Docker and Docker Compose (optional)

## Getting Started

### 1. Environment Setup

1. Clone the repository:

```bash
git clone <repository-url>
cd manthrabin_backend
```

2. Create and activate a virtual environment:

```bash
python -m venv .venv
source .venv/bin/activate  # On Windows: .venv\Scripts\activate
```

3. Install dependencies:

```bash
pip install -r requirements.txt
```

or if it's not the first time you run this project, make sure you installed pip-tools `pip install 'pip-tools==7.4.1'`, and then

```bash
pip-sync
```

4. Set up environment variables:

```bash
cp .env.example .env
```

5. Edit `.env` with your configurations.

### 2. Database Setup

#### Run ElasticSearch Container

```bash
docker run --network=host -m 1GB -e "discovery.type=single-node" -e ELASTICSEARCH_USERNAME=elastic -e ELASTICSEARCH_PASSWORD=12345678 -e "xpack.security.enabled=false" -e "xpack.security.enrollment.enabled=false" docker.mobinhost.com/library/elasticsearch:8.17.4
```

> it will be published on port 9200 on localhost
---

1. Configure your database settings in `manthrabin_backend/config.py`
2. Run migrations:

```bash
python manage.py makemigrations
python manage.py migrate
```

3. Create a superuser:

```bash
python manage.py createsuperuser
```

### 3. Running the Development Server

```bash
python manage.py runserver
```

The API will be available at `http://localhost:8000`

## Project Structure

### Key Components

#### 1. Users App

- Handles authentication and user management

#### 2. Documents App

- Manages document upload and processing

#### 3. Conversations App

- Handles chat functionality with AI

## API Documentation

- Swagger UI: `http://localhost:8000/api/docs/`
- ReDoc: `http://localhost:8000/api/redoc/`

## Docker Support (Under construction...)

To run the project using Docker:

```bash
# Build and start the backend and its database, Redis, and Elasticsearch dependencies
docker compose up --build backend

# Run migrations
docker compose exec backend python manage.py migrate
```

## Testing (Under construction...)

Run tests using:

```bash
python manage.py test
```

---

## Common Issues and Solutions

### Elasticsearch Connection Issues

- Ensure Elasticsearch container is running
- Check ES_URL and ES_PORT in .env
- Verify ES_USER and ES_PASS credentials
