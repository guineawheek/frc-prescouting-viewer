Make an application that does the following, given a FIRST Robotics Competition event code: (e.g. "2026gal")

* Display on the left hand side a table of every team in the division, where they're from, and their EPA as fetched from https://www.statbotics.io/ using their REST API at https://www.statbotics.io/docs/rest
 * You should be able to sort the table by EPA.
 * EPA should be locally cached in a sqlite database with a button in the UI to refresh EPAs from Statbotics.
 * Statbotics has a tendency to go down quite often, so being conservative with API requests to Statbotics is a core design requirement, hence the caching.
* When a team in the left-side table is selected, a display on the right hand side with a list of their matches from past and current events should be displayed, similar to The Blue Alliance. Data should be fetched from The Blue Alliance's API.
  * An API key is provided in `tba_api.json` and is excluded from Git.
* Past team matches should be selectable, and when selected, an embedded YouTube player should appear below the match view with the video associated with the match, if any.

A rough mockup of this UI is located in `LAYOUT.png` and is intended as a general sketch for how this UI should work.