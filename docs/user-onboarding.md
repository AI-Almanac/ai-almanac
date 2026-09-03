# AI Almanac User Onboarding Tutorial Plan

## Software

Using the latest version of driver.js to implement an interactive onboarding tutorial.

## Overview

The tutorial is split into several discrete modules:

1. The landing page
2. Benchmarking
3. Blending
4. Forecasting

The landing page turoial should just welcome users, point them to the
other features, and encourage the users to use the LLM for help. Each
of the main feature pages should have their own tutorials that are
self-contained. We should add a help icon or something somewhere
obivous, but not too obstrusive that users can click to restart a
tutorial.

We should only show the tutorial the first time a user vists the site
or a feature, but make it easy to restart the tutorial at any time.

Keep details focused on getting the user up to speed on the workflows
available in the platform. We don't need to walk the user through
every single option in the benchmark configuration, just the basics to
get them rolling. We can provide more detailed documentation for all
of the specific options elsewhere. If the tutuorial is too dense,
people will skip it.

### The landing page

Show a welcome message, point to the other features, encourage the
user to ask the LLM for assistance.

### Benchmarking

Walk the user through the example benchmark results showing the main
features of the map and how to find different metrics.

Move to setting up and running their own benchmark.

### Blending

Expalin at a high level what blending means in the context of the
platform and what the purpose is.

Move to setting up and training a new blend.

### Forecasts

Walk the user through the example forecast on the forecasts
page. Explain how the interface is set up and how to interpret the
probablistic forecast display.

Move to running a new forecast on from a trained blend.


## Detailed Steps

### Landing Page

1. Show a brief `Getting Started` message that explains the purpose
   and what the main features of the platform are. It should be as
   succinct as possible while clearly indicating to the user what is
   possible. This should be a jumping off point for users to navigate
   to any of the other features.
2. Highlight the LLM chat interface and encourage the user to ask the
   LLM for assistance.
3. If the user is still clicking though the tutorial, take them to the
   benchmarks page.
   
   
### Benchmarks

-- The user navigates to the benchmark page manually or via the landing page tutorial --

1. Breif welcome message explaining the concept of the benchmark
   system, i.e. determinisitc and probablistic metrics for AIWP models
   ability to predict the onset of the rainy season.
2. Highlight the map and map crontols
3. Highlight the probabilistic metrics tab
4. Highlight the raw metrics and quantiles below the map and the
   ability to subset the metrics to a smaller lat-lon boundaries.
5. Highlight the benchmark summary panel
6. Highlight the 'New Benchmark' button and take the user to the benchmark setup page.

#### Benchmark Setup

-- The user found this page on their own (direct navigation) or got
here from the benchmark tutorial --

1. Show the user the Benchmark Plan
2. Encourage the user to use the LLM to setup the benchmark
3. Highlight the manual configuration option
4. Walk the user through the main sections of the manual configuration.

### Blends

-- the user navigates to the blends page manually from the nav bar, or
from the last step of the landing page walkthough --

1. Show the user the blend metrics
2. Show the user the spatial maps
3. Show the user the downloadable outputs
4. Encourage the user to use the LLM chat to understand the results
   and setup a new blend.
5. Highlight the 'New Blend Button'

#### Blend Setup

-- The user found this page on their own (direct navigation) or got
here from the blend walkthrough --

1. Walk the user through the main steps to setup a blend
2. Encourage the user to use the LLM to setup and understand the options.


### Forecasts

-- the user navigates to the forecasts page manually from the nav bar,
or from the last step of the landing page walkthough --

1. Highlight the map interface
2. Highlight the map controls
3. Encourage the user to click one of the cells/regions to see a
   detailed breakdown of the forecast probabliites for that cell.
