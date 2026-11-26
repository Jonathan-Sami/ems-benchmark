FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1

# numpy/scipy bewusst auf 1.x gepinnt: die HAOS-VM-CPUs bieten kein x86-64-v2,
# numpy-2.x-Wheels crachen dort mit "doesn't support (X86_V2)".
RUN pip install --no-cache-dir "numpy==1.26.4" "scipy==1.11.4"

COPY benchmark /opt/benchmark

CMD [ "python3", "/opt/benchmark/daemon.py" ]
