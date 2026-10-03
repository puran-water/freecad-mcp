<#
.SYNOPSIS
Installs the narrowly scoped inbound rule required only for WSL NAT access.

.DESCRIPTION
Run from an elevated PowerShell prompt. Mirrored-networking and native
loopback installations do not need an inbound rule. Wildcard/Any remote
addresses are refused deliberately.
#>
[CmdletBinding(SupportsShouldProcess = $true)]
param(
    [Parameter(Mandatory = $true)]
    [ValidateScript({ Test-Path -LiteralPath $_ -PathType Leaf })]
    [string]$FreeCADExe,

    [Parameter(Mandatory = $true)]
    [ValidateNotNullOrEmpty()]
    [string]$BindAddress,

    [Parameter(Mandatory = $true)]
    [ValidateNotNullOrEmpty()]
    [string]$RemoteAddress,

    [ValidateRange(1, 65535)]
    [int]$Port = 9875,

    [string]$RuleName = "PuranOS FreeCAD MCP (scoped WSL)"
)

$forbidden = @("*", "Any", "0.0.0.0/0", "::/0")
if ($forbidden -contains $RemoteAddress) {
    throw "RemoteAddress must be the exact WSL address or a narrowly scoped WSL subnet."
}
if ($forbidden -contains $BindAddress) {
    throw "BindAddress must name the exact Windows host interface; wildcard binds are forbidden."
}

$principal = New-Object Security.Principal.WindowsPrincipal(
    [Security.Principal.WindowsIdentity]::GetCurrent()
)
if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    throw "Run this script from an elevated PowerShell prompt."
}

if ($PSCmdlet.ShouldProcess($RuleName, "replace scoped inbound firewall rule")) {
    Get-NetFirewallRule -DisplayName $RuleName -ErrorAction SilentlyContinue |
        Remove-NetFirewallRule
    New-NetFirewallRule `
        -DisplayName $RuleName `
        -Direction Inbound `
        -Action Allow `
        -Protocol TCP `
        -LocalPort $Port `
        -LocalAddress $BindAddress `
        -RemoteAddress $RemoteAddress `
        -Program (Resolve-Path -LiteralPath $FreeCADExe).Path `
        -Profile Private
}
