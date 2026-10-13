These unmodified Nextcloud App Store schema files were retrieved on 2026-10-10.

- `info.xsd`: https://apps.nextcloud.com/schema/apps/info.xsd
- `pre-info.xslt`: https://raw.githubusercontent.com/nextcloud/appstore/master/nextcloudappstore/api/v1/release/pre-info.xslt

License: AGPL-3.0-or-later. The Store transforms the manifest first, removing
ExApp-only extensions, then validates the result against the base schema.
Direct XSD validation incorrectly rejects ExApp route definitions.
