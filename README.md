# Space Station Inventory Management System

## Project Overview
Flask-based API for optimizing item placement and management in space station environments.

## Key Features
- Intelligent 3D bin-packing algorithm for item placement
- Waste identification and management system
- Time simulation capabilities
- Comprehensive activity logging
- Container space optimization

## Setup Instructions

### Docker Deployment
```bash
docker build -t space-inventory .
docker run -p 8000:8000 space-inventory
```

### Local Installation
```bash
pip install -r requirements.txt
python app.py
```

## API Documentation

### Core Endpoints
| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/items` | GET | List all inventory items |
| `/api/retrieve` | POST | Remove item from storage |
| `/api/place` | POST | Optimally place new item |

### Simulation Endpoints
| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/simulate/day` | POST | Advance simulation by days |
| `/api/simulate/reset` | POST | Reset simulation state |

## Example Usage

### Retrieve an item
```bash
curl -X POST -H "Content-Type: application/json" \
-d '{"itemId":"OXYGEN-01","userId":"ASTRONAUT-42"}' \
http://localhost:8000/api/retrieve
```

### Simulate 30 days
```bash
curl -X POST -H "Content-Type: application/json" \
-d '{"days":30}' \
http://localhost:8000/api/simulate/day
```

## Data Files
- `items.json`: Item specifications and properties
- `placement.json`: Current storage arrangements
- `logs.json`: System activity history
- `containers.json`: Storage container definitions

## Optimization Algorithm
1. Multi-dimensional bin-packing
2. Weight and priority considerations
3. Orientation optimization
4. Zone-based placement rules
5. Real-time space tracking

## License
[MIT License](LICENSE)
