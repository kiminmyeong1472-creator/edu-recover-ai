FROM vllm/vllm-openai:v0.15.1
WORKDIR /opt/nemotron
COPY verify_parser.py serve.py /opt/nemotron/
RUN python3 /opt/nemotron/verify_parser.py
ENV VLLM_NO_USAGE_STATS=1 DO_NOT_TRACK=1 HF_HUB_DISABLE_TELEMETRY=1
ENTRYPOINT ["python3", "/opt/nemotron/serve.py"]
