$ErrorActionPreference = 'Stop'
$request = [Console]::ReadLine() | ConvertFrom-Json
Add-Type -AssemblyName System.Speech
$synth = New-Object System.Speech.Synthesis.SpeechSynthesizer
try {
    $voice = $synth.GetInstalledVoices() | Where-Object {
        $_.Enabled -and $_.VoiceInfo.Culture.TwoLetterISOLanguageName -eq 'zh'
    } | Select-Object -First 1
    if (-not $voice) { throw 'No Chinese desktop SAPI voice installed. Install a Chinese speech voice in Windows Settings.' }
    $synth.SelectVoice($voice.VoiceInfo.Name)
    $synth.Rate = $request.rate
    $synth.Volume = $request.volume
    $synth.Speak($request.text)
} finally {
    $synth.Dispose()
}

