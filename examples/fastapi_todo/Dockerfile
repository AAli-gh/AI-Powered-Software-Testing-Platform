FROM python:3.12-slim AS build
WORKDIR /app
COPY . .
RUN pip install .

FROM build AS runtime
CMD ["uvicorn", "todo.main:app", "--host", "0.0.0.0"]
