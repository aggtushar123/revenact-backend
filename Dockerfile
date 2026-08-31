FROM python:3.13-slim

WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

EXPOSE 8000

# Dev-oriented default (matches `manage.py runserver` used outside Docker).
# Swap for a WSGI server (gunicorn) command at actual deploy time.
CMD ["python", "manage.py", "runserver", "0.0.0.0:8000"]
