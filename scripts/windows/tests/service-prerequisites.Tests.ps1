$Library = Join-Path (Split-Path -Parent $PSScriptRoot) "service-prerequisites.ps1"
. $Library

Describe "QuuDet Windows service prerequisites" {
    It "parses PostgreSQL driver URLs into the configured host and port" {
        $endpoint = ConvertTo-QuuDetEndpoint `
            -Url "postgresql+psycopg2://user:password@db.example.test:5544/quudet" `
            -Label "database" `
            -DefaultPort 5432

        $endpoint.IsLocalFile | Should Be $false
        $endpoint.Host | Should Be "db.example.test"
        $endpoint.Port | Should Be 5544
    }

    It "does not require a TCP listener for a SQLite database" {
        $endpoint = ConvertTo-QuuDetEndpoint `
            -Url "sqlite:///./data/quudet.db" `
            -Label "database" `
            -DefaultPort 5432

        $endpoint.IsLocalFile | Should Be $true
        $endpoint.Host | Should BeNullOrEmpty
    }

    It "uses standard ports when URLs omit them" {
        $endpoint = ConvertTo-QuuDetEndpoint `
            -Url "redis://localhost/0" `
            -Label "Redis" `
            -DefaultPort 6379

        $endpoint.Host | Should Be "localhost"
        $endpoint.Port | Should Be 6379
    }
}
