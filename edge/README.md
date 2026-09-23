# edge

Software del gateway edge (NVIDIA Jetson / PC industrial): captura de cámaras,
inferencia de visión, cliente OPC UA y publicación MQTT con buffer local.

Se desarrolla en el **Sprint 3** (pipeline de visión) y el **Sprint 6** (OPC UA).
Mientras tanto, `simulator/` publica exactamente los mismos tópicos y payloads que
publicará el edge, así que la plataforma se puede desarrollar en paralelo.
