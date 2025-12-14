$headers = @{ "Content-Type" = "application/json" }
$url = "http://localhost:8000"

# Test Preview
$previewPayload = @{
    url         = "http://example.com"
    max_pages   = 1
    render_mode = "http"
} | ConvertTo-Json

try {
    Write-Host "Testing Preview..."
    $response = Invoke-RestMethod -Uri "$url/crawl-preview" -Method Post -Headers $headers -Body $previewPayload
    Write-Host "Preview Success! Pages found: $($response.Count)"
}
catch {
    Write-Host "Preview Failed: $($_.Exception.Message)"
    if ($_.Exception.Response) {
        $reader = New-Object System.IO.StreamReader($_.Exception.Response.GetResponseStream())
        Write-Host "Response: $($reader.ReadToEnd())"
    }
}

# Test Generate
$generatePayload = @{
    url         = "http://example.com"
    max_pages   = 1
    render_mode = "http"
} | ConvertTo-Json

try {
    Write-Host "Testing Generate..."
    $response = Invoke-RestMethod -Uri "$url/generate" -Method Post -Headers $headers -Body $generatePayload
    Write-Host "Generate Success! Content length: $($response.Length)"
}
catch {
    Write-Host "Generate Failed: $($_.Exception.Message)"
    if ($_.Exception.Response) {
        $reader = New-Object System.IO.StreamReader($_.Exception.Response.GetResponseStream())
        Write-Host "Response: $($reader.ReadToEnd())"
    }
}
