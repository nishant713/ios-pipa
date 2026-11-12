Add-Type -AssemblyName System.Runtime.WindowsRuntime
$asTaskGeneric = [System.WindowsRuntimeSystemExtensions].GetMethods() | Where-Object {
    $_.Name -eq 'AsTask' -and $_.GetParameters().Count -eq 1 -and $_.GetParameters()[0].ParameterType.Name -eq 'IAsyncOperation`1'
}

function Await-Op($asyncOp, $type) {
    $m = $asTaskGeneric.MakeGenericMethod($type)
    $task = $m.Invoke($null, @($asyncOp))
    $task.Wait()
    return $task.Result
}

[Windows.Storage.StorageFile, Windows.Storage, ContentType = WindowsRuntime] | Out-Null
[Windows.Media.Ocr.OcrEngine, Windows.Foundation, ContentType = WindowsRuntime] | Out-Null
[Windows.Graphics.Imaging.BitmapDecoder, Windows.Graphics, ContentType = WindowsRuntime] | Out-Null
[Windows.Graphics.Imaging.SoftwareBitmap, Windows.Graphics, ContentType = WindowsRuntime] | Out-Null
[Windows.Storage.Streams.IRandomAccessStream, Windows.Storage.Streams, ContentType = WindowsRuntime] | Out-Null
[Windows.Media.Ocr.OcrResult, Windows.Foundation, ContentType = WindowsRuntime] | Out-Null

function Run-Ocr($filePath) {
    $fileOp = [Windows.Storage.StorageFile]::GetFileFromPathAsync($filePath)
    $file = Await-Op $fileOp ([Windows.Storage.StorageFile])
    
    $streamOp = $file.OpenAsync([Windows.Storage.FileAccessMode]::Read)
    $stream = Await-Op $streamOp ([Windows.Storage.Streams.IRandomAccessStream])
    
    $decOp = [Windows.Graphics.Imaging.BitmapDecoder]::CreateAsync($stream)
    $decoder = Await-Op $decOp ([Windows.Graphics.Imaging.BitmapDecoder])
    
    $bmpOp = $decoder.GetSoftwareBitmapAsync()
    $bitmap = Await-Op $bmpOp ([Windows.Graphics.Imaging.SoftwareBitmap])
    
    $engine = [Windows.Media.Ocr.OcrEngine]::TryCreateFromUserProfileLanguages()
    $resOp = $engine.RecognizeAsync($bitmap)
    $result = Await-Op $resOp ([Windows.Media.Ocr.OcrResult])
    return $result.Text
}

Write-Output "=== CROP1 ==="
Run-Ocr (Resolve-Path 'crop1.jpg').Path
Write-Output "=== CROP2 ==="
Run-Ocr (Resolve-Path 'crop2.jpg').Path
