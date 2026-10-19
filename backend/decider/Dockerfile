FROM public.ecr.aws/lambda/python:3.12
ARG DECIDER_COMMIT=930e6d5de16309be32579f5631a7369a3322140c
RUN dnf install -y git && dnf clean all
RUN pip install --no-cache-dir torch==2.10.0 --index-url https://download.pytorch.org/whl/cpu
RUN pip install --no-cache-dir "strands-decider @ git+https://github.com/strands-labs/strands-decider.git@${DECIDER_COMMIT}"
ENV HF_HOME=/opt/model-cache
COPY download_model.py ${LAMBDA_TASK_ROOT}/download_model.py
RUN python download_model.py
COPY handler.py ${LAMBDA_TASK_ROOT}/handler.py
ENV HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 TOKENIZERS_PARALLELISM=false
CMD ["handler.handler"]
