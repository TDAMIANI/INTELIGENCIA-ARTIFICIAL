COMPOSE = docker compose -f infra/docker-compose.yml

.PHONY: up down logs test test-platform test-simulator sim-dry

up:            ## Levanta todo el entorno de desarrollo
	$(COMPOSE) up -d --build

down:          ## Baja el entorno y borra los volúmenes
	$(COMPOSE) down -v

logs:
	$(COMPOSE) logs -f --tail 50

test: test-platform test-simulator

test-platform:
	cd platform && python -m pytest -q

test-simulator:
	cd simulator && python -m pytest -q

sim-dry:       ## Muestra la telemetría simulada en consola (sin broker)
	cd simulator && python -m mvsim.publisher --dry-run --speedup 10 --duration 120 \
		--fault idler_bearing:target=12,start=0,ramp=60
