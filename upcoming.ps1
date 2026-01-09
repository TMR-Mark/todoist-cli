# Set the path to the todoist executable
$todoist = ".\todoist.exe"

# Generate CSV and import it
.\todoist.exe --csv list | Out-File -FilePath tasks.csv -Encoding utf8
$tasks = Import-Csv tasks.csv -Header ID,Priority,Date,Project,Labels,Content

$today = Get-Date
$todayStart = $today.Date 
$nextWeek = $today.AddDays(7)

$allTasks = foreach ($t in $tasks) {
    $hasTime = $false
    $dt = $null
    
    if ($t.Date -match '(\d{2})/(\d{2})/(\d{2}).*?(\d{2}:\d{2})') {
        $datePart = "20$($matches[1])-$($matches[2])-$($matches[3])"
        $timePart = $matches[4]
        $dt = [DateTime]::Parse("$datePart $timePart")
        $hasTime = $true
    } 
    elseif ($t.Date -match '(\d{2})/(\d{2})/(\d{2})') {
        $datePart = "20$($matches[1])-$($matches[2])-$($matches[3])"
        $dt = [DateTime]::Parse($datePart)
    }

    if ($dt -and $dt -le $nextWeek) {
        $overdue = $dt -lt $todayStart
        [PSCustomObject]@{
            ID = $t.ID
            Day = $dt.ToString("ddd")
            DateStr = "$($matches[2])/$($matches[3])/$($matches[1])"
            Time = if ($hasTime) { $dt.ToString("hh:mm tt") } else { "        " }
            Content = $t.Content
            SortDate = $dt
            Status = if ($overdue) { "Overdue" } else { "Upcoming" }
        }
    }
}

# Output sorted tasks with status
if ($allTasks) {
    $allTasks | Sort-Object SortDate | Select-Object ID, Day, DateStr, Time, Content, Status | ConvertTo-Csv -NoTypeInformation
}

# Cleanup
if (Test-Path tasks.csv) { Remove-Item tasks.csv }
