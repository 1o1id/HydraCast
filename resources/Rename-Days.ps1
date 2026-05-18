# Clear the screen for a clean prompt
Clear-Host

# 1. Prompt for the target folder
$targetDir = Read-Host "Enter the full path of the folder"
$targetDir = $targetDir.Trim('"').Trim("'")

if (-Not (Test-Path -Path $targetDir -PathType Container)) {
    Write-Host "Error: The specified folder does not exist." -ForegroundColor Red
    Pause
    exit
}

# 2. Prompt for the renaming layout
Write-Host "`nSelect Renaming Option:" -ForegroundColor Cyan
Write-Host "1. Short Underscore to Long Underscore   (e.g., _MON_ to _MONDAY_)"
Write-Host "2. Long Underscore to Short Underscore   (e.g., _MONDAY_ to _MON_)"
Write-Host "3. Parentheses to Short Underscore       (e.g., (MON) or (MONDAY) to _MON_)"
Write-Host "4. Parentheses to Double Underscore Long (e.g., (MON) or (MONDAY) to __MONDAY__)"
$choice = Read-Host "Enter 1, 2, 3, or 4"

if ($choice -notin @('1', '2', '3', '4')) {
    Write-Host "Invalid choice. Exiting..." -ForegroundColor Red
    Pause
    exit
}

# 3. Define the day mappings
$days = @(
    [pscustomobject]@{Short="MON"; Long="MONDAY"},
    [pscustomobject]@{Short="TUE"; Long="TUESDAY"},
    [pscustomobject]@{Short="WED"; Long="WEDNESDAY"},
    [pscustomobject]@{Short="THU"; Long="THURSDAY"},
    [pscustomobject]@{Short="FRI"; Long="FRIDAY"},
    [pscustomobject]@{Short="SAT"; Long="SATURDAY"},
    [pscustomobject]@{Short="SUN"; Long="SUNDAY"}
)

Write-Host "`nProcessing files and folders in: $targetDir" -ForegroundColor Yellow
Write-Host "--------------------------------------------------"

# Helper function to execute the renaming process
function Rename-Items {
    param($items, $searchRegex, $newStr)
    foreach ($item in $items) {
        # Perform case-insensitive regex replacement
        $newName = $item.Name -ireplace $searchRegex, $newStr
        if ($item.Name -cne $newName) {
            Write-Host "Renaming: $($item.Name)  ->  $newName" -ForegroundColor Green
            Rename-Item -Path $item.FullName -NewName $newName -ErrorAction SilentlyContinue
        }
    }
}

# 4. Loop through each weekday configuration
foreach ($day in $days) {
    $wildcards = @()
    $searchRegex = ""
    $replace = ""

    # Configure search patterns and replacements based on selection
    switch ($choice) {
        '1' {
            $wildcards = @("*_$($day.Short)_*")
            $searchRegex = "_$($day.Short)_"
            $replace = "_$($day.Long)_"
        }
        '2' {
            $wildcards = @("*_$($day.Long)_*")
            $searchRegex = "_$($day.Long)_"
            $replace = "_$($day.Short)_"
        }
        '3' {
            $wildcards = @("*($($day.Short))*", "*($($day.Long))*")
            $searchRegex = "\(($($day.Short)|$($day.Long))\)"
            $replace = "_$($day.Short)_"
        }
        '4' {
            $wildcards = @("*($($day.Short))*", "*($($day.Long))*")
            $searchRegex = "\(($($day.Short)|$($day.Long))\)"
            $replace = "__$($day.Long)__"
        }
    }

    # Step A: Gather and Rename Files
    $files = @()
    foreach ($wc in $wildcards) {
        $files += Get-ChildItem -Path $targetDir -Recurse -File -Filter $wc -ErrorAction SilentlyContinue
    }
    $files = $files | Sort-Object FullName -Unique | Where-Object { $_.Name -match $searchRegex }
    if ($files) { Rename-Items -items $files -searchRegex $searchRegex -newStr $replace }

    # Step B: Gather and Rename Folders (Deepest paths sorted first so folder paths don't break mid-run)
    $folders = @()
    foreach ($wc in $wildcards) {
        $folders += Get-ChildItem -Path $targetDir -Recurse -Directory -Filter $wc -ErrorAction SilentlyContinue
    }
    $folders = $folders | Sort-Object FullName -Unique | Where-Object { $_.Name -match $searchRegex } | Sort-Object -Property @{Expression={$_.FullName.Length}; Descending=$true}
    if ($folders) { Rename-Items -items $folders -searchRegex $searchRegex -newStr $replace }
}

Write-Host "--------------------------------------------------"
Write-Host "Done! All matching items have been processed." -ForegroundColor Cyan
Pause
