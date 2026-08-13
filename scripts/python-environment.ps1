function Get-PythonFailureCode {
    param(
        [Parameter(Mandatory = $true)]
        [string] $Message,

        [Parameter(Mandatory = $true)]
        [string] $FallbackCode
    )

    if ($Message -match 'Access is denied|\u62D2\u7EDD\u8BBF\u95EE|Unable to create process|\u65E0\u6CD5\u521B\u5EFA\u8FDB\u7A0B') {
        return 'PYTHON_EXECUTION_DENIED'
    }
    if ($Message -match 'No Python at|cannot find|\u627E\u4E0D\u5230') {
        return 'VENV_INCOMPLETE'
    }
    return $FallbackCode
}
