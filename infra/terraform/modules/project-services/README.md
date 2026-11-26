# project-services

Turns on the Google APIs listed in the environment's `locals.tf`. Every other module waits for
it, so a missing API fails here, by name. APIs are never turned off on destroy, because other
things in the project may use them.

| Input | |
| --- | --- |
| `project_id` | GCP project |
| `services` | API names, `<name>.googleapis.com` |

| Output | |
| --- | --- |
| `services` | The APIs turned on |
