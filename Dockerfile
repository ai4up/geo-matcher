# Base image
FROM python:3.11-slim

# Install system dependencies
RUN apt-get update && apt-get install -y curl build-essential

# Install Poetry
RUN curl -sSL https://install.python-poetry.org | python3 -
ENV PATH="/root/.local/bin:$PATH"

# Set workdir
WORKDIR /app

# Copy only necessary files first (for caching)
COPY pyproject.toml poetry.lock ./

# Install dependencies
RUN poetry config virtualenvs.create false \
 && poetry install --no-interaction --no-ansi --no-root

# Copy the rest of the code
COPY . .

# Run app with Waitress
CMD ["waitress-serve", "--host=0.0.0.0", "--port=5000", "geo_matcher.wsgi:app"]
