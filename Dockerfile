FROM python:3.11-slim

WORKDIR /app

COPY pyproject.toml README.md ./
COPY ghostwriter/ ghostwriter/

RUN pip install --no-cache-dir . gunicorn

# Pre-download NLTK data (small, avoids runtime download)
RUN python -c "\
import nltk; \
nltk.download('averaged_perceptron_tagger_eng', quiet=True); \
nltk.download('wordnet', quiet=True)"

# GloVe vectors (~128 MB) are downloaded on first run and
# cached to the persistent volume at /data/gensim-data.

RUN mkdir -p /data/poems /data/gensim-data

EXPOSE 5000

CMD ["gunicorn", \
     "--bind", "0.0.0.0:5000", \
     "--workers", "1", \
     "--threads", "4", \
     "--timeout", "300", \
     "--access-logfile", "-", \
     "ghostwriter.server:app"]
