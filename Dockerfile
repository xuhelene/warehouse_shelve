FROM python:3.11-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    TZ=Asia/Shanghai
WORKDIR /app
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt
COPY apps/ apps/
COPY core/ core/
COPY modules/ modules/
COPY solvers/ solvers/
COPY data/slotting/existing_inventory.csv data/slotting/orders.csv data/slotting/orders_split_demo.csv data/slotting/
COPY data/picking_path/ data/picking_path/
COPY data/sku.xlsx data/warehouse_layout.json data/
RUN mkdir -p results config
EXPOSE 8010
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8010/whshelve/api/modules', timeout=3)"
CMD ["python", "-m", "uvicorn", "apps.web_portal:app", "--host", "0.0.0.0", "--port", "8010"]
