Param(
    [string]$OsrmDataDir = "osrm-data",
    [string]$PbfUrl = "http://download.geofabrik.de/asia/uzbekistan-latest.osm.pbf",
    [int]$Port = 5000
)

Write-Host "=== Taxi Routing - OSRM setup ===" -ForegroundColor Cyan

$ErrorActionPreference = "Stop"

# 1) Prepare data directory`
$dataPath = Join-Path -Path (Get-Location) -ChildPath $OsrmDataDir
if (-not (Test-Path $dataPath)) {
    Write-Host "Creating data directory: $dataPath"
    New-Item -ItemType Directory -Path $dataPath | Out-Null
}

# 2) Download Uzbekistan map (if not downloaded yet)
$pbfPath = Join-Path -Path $dataPath -ChildPath "uzbekistan-latest.osm.pbf"
if (-not (Test-Path $pbfPath)) {
    Write-Host "Downloading Uzbekistan map from $PbfUrl ..."
    Invoke-WebRequest -Uri $PbfUrl -OutFile $pbfPath
} else {
    Write-Host "PBF file already exists: $pbfPath"
}

# 3) Run osrm-extract / osrm-partition / osrm-customize
Write-Host "Running osrm-extract (this may take a few minutes)..." -ForegroundColor Yellow
docker run -t -v "${dataPath}:/data" osrm/osrm-backend `
    osrm-extract -p /opt/car.lua /data/uzbekistan-latest.osm.pbf

Write-Host "Running osrm-partition..." -ForegroundColor Yellow
docker run -t -v "${dataPath}:/data" osrm/osrm-backend `
    osrm-partition /data/uzbekistan-latest.osrm

Write-Host "Running osrm-customize..." -ForegroundColor Yellow
docker run -t -v "${dataPath}:/data" osrm/osrm-backend `
    osrm-customize /data/uzbekistan-latest.osrm

# 4) Start OSRM routed server (host port $Port -> container 5000)
#    Use a different port for the Flask web app (e.g. FLASK_PORT=5002 in .env) so they do not conflict.
Write-Host "Starting OSRM routed server on port $Port ..." -ForegroundColor Green
docker run -d -p ${Port}:5000 -v "${dataPath}:/data" osrm/osrm-backend `
    osrm-routed --algorithm mld /data/uzbekistan-latest.osrm

Write-Host ""
Write-Host "OSRM should now be available at http://localhost:$Port" -ForegroundColor Green
Write-Host "Set OSRM_BASE_URL=http://localhost:$Port in your .env file." -ForegroundColor DarkGray
Write-Host "Run the Flask app on another port (e.g. FLASK_PORT=5002)." -ForegroundColor DarkGray
Write-Host "You can test it with:  curl http://localhost:$Port/route/v1/driving/69.268777,41.285062;69.213917,41.317809" -ForegroundColor DarkGray
