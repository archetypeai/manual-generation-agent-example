# Overview

# Manual Generation Agent

nick@ 26/07/06

# Overview

This document summarizes the high level design, requirements, and eval results of the Manual Generation Agent.

# Manual Generation Agent

The Manual Generation algorithm takes an input instruction and video, analyzes the video, and outputs a report indicating the key steps and corresponding timestamps within the video.

Figure: a high-level overview of the inputs/outputs to the manual generation agent.

See the [algorithm design section]() for details on the internals of the Manual Generation Agent algorithm and modules.

# Reference code

See this full end-to-end example here:

* [https://github.com/archetypeai/atai\_core/blob/main/research/agents/manual\_generation\_agent/example\_main.py](https://github.com/archetypeai/atai_core/blob/main/research/agents/manual_generation_agent/example_main.py)

Reference code for the Manual Generation Agent can be found here:

* [Manual Generation specific modules](https://github.com/archetypeai/atai_core/blob/main/research/agents/manual_generation_agent/manual_generation_agent.py)  
* [Reusable agent modules](https://github.com/archetypeai/atai_core/blob/main/research/agents/agents_framework/nodes.py)

See this [README](https://github.com/archetypeai/atai_core/blob/main/research/agents/manual_generation_agent/README.md) on how to set up and run the example (it requires an L40S or H100 and access to our model depot).

# Version Tracker & Roadmap

This document describes the initial V1 version of the Manual Generation Agent. There are no plans to go beyond V1 unless we get specific feedback from customers.

# Design Doc

# Manual Generation Agent: Algorithm Design

nick@ 26/07/06

# Overview

This section summarizes the algorithm design for the manual generation agent (MGA). This document defines MGA V1.

# Build Time Algorithm

At this time, there are no build-time optimization steps for the Manual Generation algorithm. The algorithm can be used zero-shot without any need for optimization or fine-tuning, beyond passing the SOP and video at runtime. In future versions, we may add build-time steps for searching for optimal prompts or fine-tuning the base model to under bespoke equipment or activities.

# Run Time Algorithm

The Manual Generation algorithm takes an input instruction and video, analyzes the video, and outputs a report indicating the key steps and corresponding timestamps within the video.

Figure: a high-level overview of the inputs/outputs to the manual generation agent.

The Manual Generation Agent runtime algorithm consists of the following modules:

Figure: the main steps of the manual generation agent. A prompt and video are fed to the Newton fusion model  
and it outputs the formatted manual generation results.

## Run Time Time Algorithm Description

The majority of the algorithmic logic for the manual generation agent lives directly within the fusion model, specifically within the “Video Encoder” and “Decoder Generation” steps. This uses **newton-fusion-f1-0**, our latest 35B parameter mixture-of-experts model (with 3B active parameters) that’s built on Qwen3.5VL as the base model. F1-0 supports fusion across images, video, sensor logs, and text; with the next version of the model adding timeseries then audio.

The modules surrounding the core Newton Fusion module are focused on either preprocessing the multimodal data (video \+ text) input to the fusion model or post processing the generative output of the model (text). The preprocessing video logic is core to making the task verification algorithm function on longer videos, and is focused on efficiently sampling and compressing the frames from the video to fit as much of the video into the model’s context window as possible. While f1.0 supports much larger context windows than c2.6 (256K vs 32K), even moderate videos can easily overflow the model’s context window if not sampled efficiently. To mitigate this, we use three major algorithmic techniques to efficiently sample a video for the V1 of the manual generation algorithm.

The main techniques used to sample and compress the video are identical to that used in the Task Verification Agent V1, and can be found [here](https://docs.google.com/document/d/1WrRvrG8oSgytiAbGQ1QafEtcGhP5ur4N3zKh8E4YEwc/edit?tab=t.qkf9d135r069#bookmark=id.kih7hmoyv07f).  
