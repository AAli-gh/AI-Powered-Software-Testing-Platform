ARG PYTHON=3.12
FROM python:${PYTHON}-slim AS base
WORKDIR /app
COPY requirements.txt .
RUN pip install -r requirements.txt
COPY . .
CMD ["gunicorn", "blog_project.wsgi"]
