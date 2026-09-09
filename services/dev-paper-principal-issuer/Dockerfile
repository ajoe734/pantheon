FROM python:3.12-slim
ARG GIT_SHA=unknown
LABEL org.opencontainers.image.revision=$GIT_SHA
WORKDIR /issuer
COPY scripts/issue_dev_paper_principals.py /issuer/issue_dev_paper_principals.py
ENTRYPOINT ["python", "/issuer/issue_dev_paper_principals.py"]
CMD ["--serve"]
