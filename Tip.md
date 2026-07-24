conda activate quudet01

($env:SYNC_JOBS="true") #不用输入
cd quudet-yolo-lab-backend
cd /d D:\yolo26\quudet-yolo-lab-backend
uvicorn app.main:app --host 127.0.0.1 --port 8000 --log-level info 

cd /d D:\yolo26\quudet-yolo-lab
cd D:\yolo26\quudet-yolo-lab
python -m http.server 8080


