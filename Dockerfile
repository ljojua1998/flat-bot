# ბოტის კონტეინერი. მსუბუქი Python-ის იმიჯი.
FROM python:3.12-slim

# ლოგები მაშინვე გამოჩნდეს (არ დაბუფერდეს)
ENV PYTHONUNBUFFERED=1

WORKDIR /app

# ჯერ მხოლოდ requirements — ქეშის უკეთ გამოსაყენებლად
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# მერე დანარჩენი კოდი
COPY . .

# ბაზა ამ საქაღალდეში ინახება (compose-ში ვმაუნთავთ host-ის ./data-ზე)
ENV DB_PATH=/data/data.sqlite3

CMD ["python", "main.py"]
