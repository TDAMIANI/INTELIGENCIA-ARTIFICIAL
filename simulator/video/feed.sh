#!/bin/sh
# Publica cámaras simuladas en el servidor RTSP (mediamtx).
# Si existe /samples/<cámara>.mp4 lo reproduce en loop (videos reales de correas);
# si no, genera una señal de prueba para poder desarrollar igual.
set -eu
RTSP_URL="${RTSP_URL:-rtsp://rtsp:8554}"
CAMERAS="${CAMERAS:-cv-201-rgb-01 cv-201-th-01}"
ENCODE="-c:v libx264 -preset ultrafast -tune zerolatency -g 30 -pix_fmt yuv420p -an"

feed() {
  cam="$1"
  while true; do
    if [ -f "/samples/${cam}.mp4" ]; then
      echo "[${cam}] reproduciendo /samples/${cam}.mp4"
      ffmpeg -hide_banner -loglevel warning -re -stream_loop -1 -i "/samples/${cam}.mp4" \
        $ENCODE -f rtsp -rtsp_transport tcp "${RTSP_URL}/${cam}" || true
    else
      case "$cam" in
        *-th-*) filter="format=gray,pseudocolor=preset=magma" ;;  # aspecto de termografía
        *)      filter="null" ;;
      esac
      echo "[${cam}] sin video de muestra, generando señal de prueba"
      ffmpeg -hide_banner -loglevel warning -re -f lavfi -i "testsrc2=size=1280x720:rate=15" \
        -vf "${filter},drawtext=text='${cam} (simulada)':x=20:y=20:fontsize=36:fontcolor=white:box=1:boxcolor=black@0.5" \
        $ENCODE -f rtsp -rtsp_transport tcp "${RTSP_URL}/${cam}" || true
    fi
    echo "[${cam}] ffmpeg terminó, reintentando en 3 s"
    sleep 3
  done
}

for cam in $CAMERAS; do
  feed "$cam" &
done
wait
