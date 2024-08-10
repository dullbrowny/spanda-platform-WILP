from fastapi import FastAPI, WebSocket, File, UploadFile, status, HTTPException, Request, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from ollama import chat as ollama_chat
from httpx import AsyncClient
import aiohttp

import json
import httpx
import re , asyncio
import zipfile
import ollama
from pydantic import BaseModel
import base64
import logging 
from typing import List
from fastapi import BackgroundTasks
import os
from pathlib import Path
from dotenv import load_dotenv
from starlette.websockets import WebSocketDisconnect
from goldenverba.server.types import CourseIDRequest 
from wasabi import msg  # type: ignore[import]
import time
# from goldenverba.server.bitsp import(
#     ollama_afe,
#     ollama_aga,
#     ollama_aqg
# )
import logging
from goldenverba import verba_manager
from goldenverba.server.types import (
    ResetPayload,
    ConfigPayload,
    QueryPayload,
    GeneratePayload,
    GetDocumentPayload,
    SearchQueryPayload,
    ImportPayload,
    QueryRequest,
    MoodleRequest,
    QueryRequestaqg
)

from goldenverba.server.spanda_utils import chatbot
import requests
from docx import Document
import fitz  # PyMuPDF
import pytesseract
from PIL import Image
import io
import re
import csv
import httpx
import asyncio
from goldenverba.server.util import get_config, set_config, setup_managers
logger = logging.getLogger("API")
load_dotenv()
# Replace with your Moodle instance URL and token

MOODLE_URL = os.getenv('MOODLE_URL')
TOKEN = os.getenv('TOKEN')

# Function to make a Moodle API call
def moodle_api_call(params, extra_params=None):
    if extra_params:
        params.update(extra_params)
    endpoint = f'{MOODLE_URL}/webservice/rest/server.php'
    response = requests.get(endpoint, params=params)
    print(f"API Call to {params['wsfunction']} - Status Code: {response.status_code}")
    print(f"API Request URL: {response.url}")  # Log the full URL for debugging

    try:
        result = response.json()
    except ValueError as e:
        raise ValueError(f"Error parsing JSON response: {response.text}") from e

    if 'exception' in result:
        raise Exception(f"Error: {result['exception']['message']}")

    return result
# Check if runs in production
production_key = os.environ.get("VERBA_PRODUCTION", "")
tag = os.environ.get("VERBA_GOOGLE_TAG", "")
if production_key == "True":
    msg.info("API runs in Production Mode")
    production = True
else:
    production = False

manager = verba_manager.VerbaManager()
setup_managers(manager)

# FastAPI App
app = FastAPI()

origins = [
    "http://localhost:3000",
    "https://verba-golden-ragtriever.onrender.com",
    "http://localhost:8000",
    "http://localhost:1511",
    "http://localhost/moodle", 
    "http://localhost", 
    "https://taxila-spanda.wilp-connect.net",
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

BASE_DIR = Path(__file__).resolve().parent

# Serve the assets (JS, CSS, images, etc.)
app.mount(
    "/static/_next",
    StaticFiles(directory=BASE_DIR / "frontend/out/_next"),
    name="next-assets",
)

# Serve the main page and other static files
app.mount("/static", StaticFiles(directory=BASE_DIR / "frontend/out"), name="app")


@app.get("/")
@app.head("/")
async def serve_frontend():
    return FileResponse(os.path.join(BASE_DIR, "frontend/out/index.html"))

### GET

# Define health check endpoint
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

@app.get("/api/health")
async def health_check():
    try:
        logger.info("Health check initiated.")
        if manager.client.is_ready():
            logger.info("Database is ready.")
            return JSONResponse(
                content={"message": "Alive!", "production": production, "gtag": tag}
            )
        else:
            logger.warning("Database not ready.")
            return JSONResponse(
                content={
                    "message": "Database not ready!",
                    "production": production,
                    "gtag": tag,
                },
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            )
    except Exception as e:
        logger.error(f"Healthcheck failed with {str(e)}")
        return JSONResponse(
            content={
                "message": f"Healthcheck failed with {str(e)}",
                "production": production,
                "gtag": tag,
            },
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        )

# Get Status meta data
@app.get("/api/get_status")
async def get_status():
    try:
        schemas = manager.get_schemas()
        sorted_schemas = dict(
            sorted(schemas.items(), key=lambda item: item[1], reverse=True)
        )

        sorted_libraries = dict(
            sorted(
                manager.installed_libraries.items(),
                key=lambda item: (not item[1], item[0]),
            )
        )
        sorted_variables = dict(
            sorted(
                manager.environment_variables.items(),
                key=lambda item: (not item[1], item[0]),
            )
        )

        data = {
            "type": manager.weaviate_type,
            "libraries": sorted_libraries,
            "variables": sorted_variables,
            "schemas": sorted_schemas,
            "error": "",
        }

        msg.info("Status Retrieved")
        return JSONResponse(content=data)
    except Exception as e:
        data = {
            "type": "",
            "libraries": {},
            "variables": {},
            "schemas": {},
            "error": f"Status retrieval failed: {str(e)}",
        }
        msg.fail(f"Status retrieval failed: {str(e)}")
        return JSONResponse(content=data)

# Get Configuration
@app.get("/api/config")
async def retrieve_config():
    try:
        config = get_config(manager)
        msg.info("Config Retrieved")
        return JSONResponse(status_code=200, content={"data": config, "error": ""})

    except Exception as e:
        msg.warn(f"Could not retrieve configuration: {str(e)}")
        return JSONResponse(
            status_code=500,
            content={
                "data": {},
                "error": f"Could not retrieve configuration: {str(e)}",
            },
        )

### WEBSOCKETS

@app.websocket("/ws/generate_stream")
async def websocket_generate_stream(websocket: WebSocket):
    await websocket.accept()
    while True:  # Start a loop to keep the connection alive.
        try:
            data = await websocket.receive_text()
            # Parse and validate the JSON string using Pydantic model
            payload = GeneratePayload.model_validate_json(data)
            msg.good(f"Received generate stream call for {payload.query}")
            full_text = ""
            async for chunk in manager.generate_stream_answer(
                [payload.query], [payload.context], payload.conversation
            ):
                full_text += chunk["message"]
                if chunk["finish_reason"] == "stop":
                    chunk["full_text"] = full_text
                await websocket.send_json(chunk)

        except WebSocketDisconnect:
            msg.warn("WebSocket connection closed by client.")
            break  # Break out of the loop when the client disconnects

        except Exception as e:
            msg.fail(f"WebSocket Error: {str(e)}")
            await websocket.send_json(
                {"message": e, "finish_reason": "stop", "full_text": str(e)}
            )
        msg.good("Succesfully streamed answer")

### POST

# Reset Verba
@app.post("/api/reset")
async def reset_verba(payload: ResetPayload):
    if production:
        return JSONResponse(status_code=200, content={})

    try:
        if payload.resetMode == "VERBA":
            manager.reset()
        elif payload.resetMode == "DOCUMENTS":
            manager.reset_documents()
        elif payload.resetMode == "CACHE":
            manager.reset_cache()
        elif payload.resetMode == "SUGGESTIONS":
            manager.reset_suggestion()
        elif payload.resetMode == "CONFIG":
            manager.reset_config()

        msg.info(f"Resetting Verba ({payload.resetMode})")

    except Exception as e:
        msg.warn(f"Failed to reset Verba {str(e)}")

    return JSONResponse(status_code=200, content={})

# Receive query and return chunks and query answer
@app.post("/api/import")
async def import_data(payload: ImportPayload):

    logging = []

    print(f"Received payload: {payload}")
    if production:
        logging.append(
            {"type": "ERROR", "message": "Can't import when in production mode"}
        )
        return JSONResponse(
            content={
                "logging": logging,
            }
        )

    try:
        set_config(manager, payload.config)
        documents, logging = manager.import_data(
            payload.data, payload.textValues, logging
        )

        return JSONResponse(
            content={
                "logging": logging,
            }
        )

    except Exception as e:
        logging.append({"type": "ERROR", "message": str(e)})
        return JSONResponse(
            content={
                "logging": logging,
            }
        )

@app.post("/api/set_config")
async def update_config(payload: ConfigPayload):

    if production:
        return JSONResponse(
            content={
                "status": "200",
                "status_msg": "Config can't be updated in Production Mode",
            }
        )

    try:
        set_config(manager, payload.config)
    except Exception as e:
        msg.warn(f"Failed to set new Config {str(e)}")

    return JSONResponse(
        content={
            "status": "200",
            "status_msg": "Config Updated",
        }
    )

# Receive query and return chunks and query answer
@app.post("/api/query")
async def query(payload: QueryPayload):
    msg.good(f"Received query: {payload.query}")
    msg.good(payload.query + "lol")
    start_time = time.time()  # Start timing
    # print(payload.course_id + "inapi.py")

    try:
        chunks, context = manager.retrieve_chunks(payload.query, payload.course_id)
        retrieved_chunks = [
            {
                "text": chunk.text,
                "doc_name": chunk.doc_name,
                "chunk_id": chunk.chunk_id,
                "doc_uuid": chunk.doc_uuid,
                "doc_type": chunk.doc_type,
                "score": chunk.score,
            }
            for chunk in chunks
        ]
        # print(retrieved_chunks)
        elapsed_time = round(time.time() - start_time, 2)  
        msg.good(f"Succesfully processed query: {payload.query} in {elapsed_time}s")    

        if len(chunks) == 0:
            return JSONResponse(
                content={
                    "chunks": [],
                    "took": 0,
                    "context": "",
                    "error": "No Chunks Available",
                }
            )

        return JSONResponse(
            content={
                "error": "",
                "chunks": retrieved_chunks,
                "context": context,
                "took": elapsed_time,
            }
        )

    except Exception as e:
        msg.warn(f"Query failed: {str(e)}")
        return JSONResponse(
            content={
                    "chunks": [],
                    "took": 0,
                    "context": "",
                    "error": f"Something went wrong: {str(e)}",
            }
        )

# Retrieve auto complete suggestions based on user input
@app.post("/api/suggestions")
async def suggestions(payload: QueryPayload):
    try:
        suggestions = manager.get_suggestions(payload.query)

        return JSONResponse(
            content={
                "suggestions": suggestions,
            }
        )
    except Exception:
        return JSONResponse(
            content={
                "suggestions": [],
            }
        )

# Retrieve specific document based on UUID
@app.post("/api/get_document")
async def get_document(payload: GetDocumentPayload):
    # TODO Standarize Document Creation
    msg.info(f"Document ID received: {payload.document_id}")

    try:
        document = manager.retrieve_document(payload.document_id)
        document_properties = document.get("properties", {})
        document_obj = {
            "class": document.get("class", "No Class"),
            "id": document.get("id", payload.document_id),
            "chunks": document_properties.get("chunk_count", 0),
            "link": document_properties.get("doc_link", ""),
            "name": document_properties.get("doc_name", "No name"),
            "type": document_properties.get("doc_type", "No type"),
            "text": document_properties.get("text", "No text"),
            "timestamp": document_properties.get("timestamp", ""),
        }

        msg.good(f"Succesfully retrieved document: {payload.document_id}")
        return JSONResponse(
            content={
                "error": "",
                "document": document_obj,
            }
        )
    except Exception as e:
        msg.fail(f"Document retrieval failed: {str(e)}")
        return JSONResponse(
            content={
                "error": str(e),
                "document": None,
            }
        )

## Retrieve and search documents imported to Weaviate
@app.post("/api/get_all_documents")
async def get_all_documents(payload: SearchQueryPayload):
    # TODO Standarize Document Creation
    msg.info("Get all documents request received")
    start_time = time.time()  # Start timing

    try:
        if payload.query == "":
            documents = manager.retrieve_all_documents(
                payload.doc_type, payload.page, payload.pageSize
            )
        else:
            documents = manager.search_documents(
                payload.query, payload.doc_type, payload.page, payload.pageSize
            )

        if not documents:
            return JSONResponse(
                content={
                    "documents": [],
                    "doc_types": [],
                    "current_embedder": manager.embedder_manager.selected_embedder,
                    "error": f"No Results found!",
                    "took": 0,
                }
            )

        documents_obj = []
        for document in documents:

            _additional = document["_additional"]

            documents_obj.append(
                {
                    "class": "No Class",
                    "uuid": _additional.get("id", "none"),
                    "chunks": document.get("chunk_count", 0),
                    "link": document.get("doc_link", ""),
                    "name": document.get("doc_name", "No name"),
                    "type": document.get("doc_type", "No type"),
                    "text": document.get("text", "No text"),
                    "timestamp": document.get("timestamp", ""),
                }
            )

        elapsed_time = round(time.time() - start_time, 2)  # Calculate elapsed time
        msg.good(
            f"Succesfully retrieved document: {len(documents)} documents in {elapsed_time}s"
        )

        doc_types = manager.retrieve_all_document_types()

        return JSONResponse(
            content={
                "documents": documents_obj,
                "doc_types": list(doc_types),
                "current_embedder": manager.embedder_manager.selected_embedder,
                "error": "",
                "took": elapsed_time,
            }
        )
    except Exception as e:
        msg.fail(f"All Document retrieval failed: {str(e)}")
        return JSONResponse(
            content={
                "documents": [],
                "doc_types": [],
                "current_embedder": manager.embedder_manager.selected_embedder,
                "error": f"All Document retrieval failed: {str(e)}",
                "took": 0,
            }
        )

# Delete specific document based on UUID
@app.post("/api/delete_document")
async def delete_document(payload: GetDocumentPayload):
    if production:
        msg.warn("Can't delete documents when in Production Mode")
        return JSONResponse(status_code=200, content={})

    msg.info(f"Document ID received: {payload.document_id}")

    manager.delete_document_by_id(payload.document_id)
    return JSONResponse(content={})

#for bitspprojs
async def make_request(query_user):
    # Escape the query to handle special characters and newlines
    formatted_query = json.dumps(query_user)

    # Create a payload with the formatted query
    payload = QueryPayload(query=formatted_query)

    # Retrieve chunks and context
    chunks, context = manager.retrieve_chunks([payload.query])
    
    return context



async def grading_assistant(question_answer_pair, context):
    user_context = "".join(context)
    rubric_content = f"""
        ## Context:
        Ensure that you grade according to the following context:

        **[CONTEXT START]**
        {context}
        **[CONTEXT END]**

        ## Instructions for Evaluation

        Please act as an impartial judge and evaluate the quality of the provided answer which attempts to address the given question based on the provided context. You will be given context, a question, and an answer to submit your reasoning and score for the correctness, comprehensiveness, and readability of the answer.

        ### Task

        Evaluate the provided answer according to the criteria below and provide scores along with explanations for each category: Correctness, Comprehensiveness, and Readability.

        ### Grading Rubric

        #### Correctness

        Evaluate whether the answer correctly addresses the question.

        - **Score 0**: The answer is completely incorrect, irrelevant, or an empty string.
        - **Example**:
            - **Question**: How to terminate a Databricks cluster?
            - **Answer**: Sorry, I don't know the answer.

        - **Score 1**: The answer provides some relevance to the question but only partially addresses it.
        - **Example**:
            - **Question**: How to terminate a Databricks cluster?
            - **Answer**: Databricks cluster is a cloud-based computing environment.

        - **Score 2**: The answer mostly addresses the question but misses or hallucinates on a critical aspect.
        - **Example**:
            - **Question**: How to terminate a Databricks cluster?
            - **Answer**: Navigate to the "Clusters" tab and find the cluster you want to terminate. Then you'll find a button to terminate all clusters at once.

        - **Score 3**: The answer correctly and completely addresses the question.
        - **Example**:
            - **Question**: How to terminate a Databricks cluster?
            - **Answer**: In the Databricks workspace, navigate to the "Clusters" tab. Find the cluster you want to terminate from the list of active clusters. Click on the down-arrow next to the cluster name to open the cluster details. Click on the "Terminate" button and confirm the action.

        #### Comprehensiveness

        Evaluate how thoroughly the answer addresses all aspects of the question.

        - **Score 0**: The answer is completely incorrect.

        - **Score 1**: The answer is correct but too brief to fully answer the question.
        - **Example**:
            - **Question**: How to use Databricks API to create a cluster?
            - **Answer**: You will need a Databricks access token.

        - **Score 2**: The answer is correct and addresses the main aspects but lacks details.
        - **Example**:
            - **Question**: How to use Databricks API to create a cluster?
            - **Answer**: You will need a Databricks access token. Set up the request URL and make the HTTP request.

        - **Score 3**: The answer is correct and fully addresses all aspects of the question.

        #### Readability

        Evaluate the readability of the answer.

        - **Score 0**: The answer is completely unreadable.

        - **Score 1**: The answer is slightly readable with irrelevant symbols or repeated words.
        - **Example**:
            - **Question**: How to use Databricks API to create a cluster?
            - **Answer**: You you you will need a Databricks access token. Then you can make the HTTP request.

        - **Score 2**: The answer is correct and mostly readable but contains minor readability issues.
        - **Example**:
            - **Question**: How to terminate a Databricks cluster?
            - **Answer**: Navigate to the "Clusters" tab. Find the cluster you want to terminate. Click on the down-arrow. Click the "Terminate" button. Click "Terminate" again to confirm.

        - **Score 3**: The answer is correct and fully readable without any issues.

        ### Format for Results

        Provide your evaluation in the following format:

        - **Correctness**:
        - Score
        - Explanation of score

        - **Readability**:
        - Score
        - Explanation of score

        - **Comprehensiveness**:
        - Score
        - Explanation of score
                            """
    payload = {
        "messages": [
            {"role": "system", "content": rubric_content},
            {"role": "user", "content": f"""Grade the following question-answer pair using the grading rubric and context provided - {question_answer_pair}"""}
        ],
        "stream": False,
        "options": {"top_k": 1, "top_p": 1, "temperature": 0, "seed": 100}
    }

    response = await asyncio.to_thread(ollama_chat, model='llama3.1', messages=payload['messages'], stream=payload['stream'])
    
    # Define a dictionary to store extracted scores
    scores_dict = {}

    # Extract the response content
    response_content = response['message']['content']

    # Define the criteria
    criteria = ["Correctness", "Readability", "Comprehensiveness"]

    # List to store individual scores
    scores = []

    for criterion in criteria:
        # Use regular expression to search for the criterion followed by 'Score:'
        criterion_pattern = re.compile(rf'{criterion}:\s*\**\s*Score\s*(\d+)', re.IGNORECASE)
        match = criterion_pattern.search(response_content)
        if match:
            # Extract the score value
            score_value = int(match.group(1).strip())
            scores.append(score_value)

    # Calculate the average score if we have scores
    avg_score = sum(scores) / len(scores) if scores else 0
    print(response['message']['content'])
    return response['message']['content'], avg_score


async def instructor_eval(instructor_name, context, score_criterion, explanation):
    # Ensure score_criterion is hashable by converting it to a string if necessary
    score_criterion = str(score_criterion)

    # Define the criterion to evaluate
    user_context = "".join(context)

    # Initialize empty dictionaries to store relevant responses and scores
    responses = {}
    scores_dict = {}

    # Evaluation prompt template
    evaluate_instructions = f"""
        -Instructions:
            You are tasked with evaluating a teacher's performance based on the criterion: {score_criterion} - {explanation}.

        -Evaluation Details:
            -Focus exclusively on the provided video transcript.
            -Ignore interruptions from student entries/exits and notifications of participants 'joining' or 'leaving' the meeting.
            -Assign scores from 1 to 5:
        -Criteria:
            -Criterion Explanation: {explanation}
            -If the transcript lacks sufficient information to judge {score_criterion}, mark it as N/A and provide a clear explanation.
            -Justify any score that is not a perfect 5.
            -Consider the context surrounding the example statements, as the context in which a statement is made is extremely important.

            Rate strictly on a scale of 1 to 5 using whole numbers only.

            Ensure the examples are directly relevant to the evaluation criterion and discard any irrelevant excerpts.
    """

    output_format = f"""Strictly follow the output format-
        -Output Format:
            -{score_criterion}: Score(range of 1 to 5, or N/A) - note: Do not use bold or italics or any formatting in this line.

            -Detailed Explanation with Examples and justification for examples:
                -Example 1: "[Quoted text from transcript]" [Description] [Timestamp]
                -Example 2: "[Quoted text from transcript]" [Description] [Timestamp]
                -Example 3: "[Quoted text from transcript]" [Description] [Timestamp]
                -...
                -Example n: "[Quoted text from transcript]" [Description] [Timestamp]
            -Include both positive and negative instances.
            -Highlight poor examples if the score is not ideal."""
    
    system_message = """You are a judge. The judge gives helpful, detailed, and polite suggestions for improvement for a particular teacher from the given context - the context contains transcripts of videos. The judge should also indicate when the judgment can be found in the context."""
    
    formatted_transcripts = f"""Here are the transcripts for {instructor_name}-   
                    [TRANSCRIPT START]
                    {user_context}
                    [TRANSCRIPT END]"""
    
    user_prompt = f"""Please provide an evaluation of the teacher named '{instructor_name}' on the following criteria: '{score_criterion}'. Only include information from transcripts where '{instructor_name}' is the instructor."""

    # Define the payload
    payload = {
        "messages": [
            {
                "role": "system",
                "content": system_message
            },
            {
                "role": "user",
                "content": formatted_transcripts + "/n/n" + evaluate_instructions + "/n/n" + user_prompt + "/n/n" + output_format
            }
        ],
        "stream": False,
        "options": {
            "top_k": 1, 
            "top_p": 1, 
            "temperature": 0, 
            "seed": 100
        }
    }

    # Asynchronous call to the LLM API
    response = await asyncio.to_thread(ollama.chat, model='llama3.1', messages=payload['messages'], stream=payload['stream'])

    # Store the response
    content = response['message']['content']

    # Extract the score from the response content
    pattern = rf'(?i)(score:\s*(\d+)|\**{re.escape(score_criterion)}\**\s*[:\-]?\s*(\d+))'
    match = re.search(pattern, content, re.IGNORECASE)

    if match:
        # Check which group matched and extract the score
        score_value = match.group(2).strip() if match.group(2) else match.group(3).strip()
        scores_dict[score_criterion] = score_value
    else:
        scores_dict[score_criterion] = "N/A"

    # Return only the relevant content without any metadata
    return {"content": content}, scores_dict


# Function to generate answer using the Ollama API
async def answer_gen(question, context):
    user_context = "".join(context)
    # One shot example given in answer_inst should be the original question + original answer.
    answer_inst = f"""
        ### Context:
        Ensure that each generated answer is relevant to the following context:

        **[CONTEXT START]**
        {context}
        **[CONTEXT END]**

        ## Answer Instructions

        You are a highly knowledgeable and detailed assistant. Please follow these guidelines when generating answers:

        ### 1. Format
        Ensure the answer is nicely formatted and visually appealing. Use:
        - Bullet points
        - Numbered lists
        - Headings
        - Subheadings where appropriate

        ### 2. Clarity
        Provide clear and concise explanations. Avoid jargon unless it is necessary, and explain it when used.

        ### 3. Math Questions
        - Include all steps in the solution process.
        - Use a clear and logical progression from one step to the next.
        - Explain each step briefly to ensure understanding.
        - Use LaTeX formatting for mathematical expressions to ensure they are easy to read and understand.

        ### 4. Non-Math Questions
        - Provide detailed explanations and context.
        - Break down complex ideas into simpler parts.
        - Use examples where appropriate to illustrate points.
        - Ensure the answer is comprehensive and addresses all parts of the question.

        ### 5. Tone
        Maintain a professional and friendly tone. Aim to be helpful and approachable.

        ### Example
        Here are a couple of examples to illustrate the format:
        ONE-SHOT-EXAMPLE-GOES-HERE"""
    
    user_prompt = f"""Please answer the following question - {question}"""

    payload = {
        "messages": [
            {
                "role": "system",
                "content": answer_inst
            },
            {
                "role": "user",
                "content": f"""Query: {user_prompt}"""
            }
        ],
        "stream": False,
        "options": {
            "top_k": 20,
            "top_p": 0.5,
            "temperature": 0.5,
            "seed": 100
        }
    }

    # Call ollama_chat function in a separate thread
    response = await asyncio.to_thread(ollama.chat, model='llama3.1', messages=payload['messages'], stream=payload['stream'])
    answer = response['message']['content']   

    return answer

# Define the endpoint
@app.post("/api/answergen")
async def answergen_ollama(request: QueryRequest):
    query = request.query
    context = await make_request(query)
    if context is None:
        raise HTTPException(status_code=500, detail="Failed to fetch context")
    
    answer = await answer_gen(query, context)
    response = {
        "answer": answer
    }
    return response


async def generate_question_variants(base_question, n, context):
    # Join the context into a single string
    user_context = "".join(context)

    base_question_gen = f"""
        **Task: Develop Distinct Numerical and Theoretical Question Variants**

        ### Context:
        Ensure that each question is relevant to the following context:

        **[CONTEXT START]**
        {context}
        **[CONTEXT END]**

        As a creative educator, your responsibility is to generate a series of unique variations of a mathematical or theoretical question. Each variation must differ both numerically and theoretically, ensuring a broad range of questions that require different analytical approaches.

        ### Objectives:
        - **Numerical and Theoretical Variations**: Modify numerical values, formulas, conditions, sequences, and theoretical concepts to create diverse challenges.
        - **Comprehensive Understanding**: Ensure that each variant promotes independent problem-solving and encourages critical thinking.
        - **Structural Integrity**: Maintain the structural integrity of the original question, especially for multi-part questions, by creating variants with corresponding multi-part sections.

        ### Instructions:

        1. **Alter Values**: Change the numerical values in the problem statement. Consider varying parameters such as initial conditions, coefficients, dimensions, or time intervals to offer new challenges.
        2. **Introduce New Variables**: Add or replace variables to change the mathematical relationships. For instance, adjust the number of terms in a sequence, the coefficients in an equation, or the dimensions in a geometry problem.
        3. **Modify Conditions**: Alter conditions, such as changing from a linear to a quadratic equation or from a direct proportion to an inverse proportion. Consider introducing new constraints that require different analytical approaches.
        4. **Change Sequences**: Modify the sequence or order of elements within a question. This can include altering arithmetic, geometric, or other numerical sequences to introduce variety and complexity. Ensure that each sequence presents a unique problem-solving scenario.
        5. **Explore Real-World Contexts**: Frame the questions in real-world scenarios, such as finance, physics, or engineering, to add practical significance and context.
        6. **Rephrase the Question**: Change the wording to focus on different theoretical aspects of the topic. Emphasize various perspectives, viewpoints, or implications.
        7. **Vary the Focus**: Shift the emphasis from one aspect of the problem to another. For instance, from causes to effects, from advantages to disadvantages, or from theoretical analysis to practical applications.
        8. **Integrate Multiple Concepts**: Combine different theoretical ideas to create complex questions that require understanding multiple concepts. Encourage connections between topics and interdisciplinary thinking.
        9. **Incorporate Current Trends**: Include recent developments, research, or trends related to the topic to make the question more relevant and engaging.

        ### Multi-Part Questions:

        1. **Maintain Structural Integrity**: Ensure that each multi-part original question has corresponding multi-part variants. The variants should reflect the same structure and format as the original, maintaining the number of parts and their relationships.
        2. **Vary Elements**: Introduce changes in each part by varying numerical values, theoretical focus, or conditions. Ensure each section of the variant provides a new and unique challenge.
        3. **Add Layers of Complexity**: Consider adding additional sub-parts or interrelated components to enhance the complexity and depth of the questions.
        4. **Promote Comprehensive Understanding**: Ensure that each part requires students to explore different facets of the problem, applying a range of techniques, concepts, or perspectives.

        ### Challenge Level:

        1. **Self-Contained Questions**: Ensure each question stands alone with sufficient complexity to discourage direct copying of answers. Avoid reliance on other questions for context.
        2. **Promote Critical Thinking**: Encourage questions that require analysis, synthesis, and evaluation rather than rote memorization or simple calculations.
        3. **Foster Analytical Skills**: Aim for questions that inspire analytical engagement, pushing students to explore various solutions and approaches.

        ### Key Points to Remember:
        - **Numerical and Theoretical Differences**: Ensure each question differs significantly in both numbers and theoretical focus, challenging students with varied problem-solving approaches.
        - **Independent Questions**: Each question must be self-contained, without relying on other questions for context.
        - **Complexity and Variety**: Introduce complexity and variety to challenge critical thinking and analytical skills.
        - **Structural Consistency in Multi-Part Questions**: Maintain structural integrity by creating multi-part variants for multi-part original questions, ensuring each part is uniquely tailored yet consistent with the original format.
        - **DO NOT GENERATE ANSWERS**

        ### Output Format - Strictly follow the output format

        #### Single-Part Questions:

        **Variant 1**: variant_1
        **Variant 2**: variant_2
        **Variant 3**: variant_3
        **Variant 4**: variant_4
        **Variant 5**: variant_5

        #### Multi-Part Questions:

        **Variant 1**: 
          a. variant_1_part_a
          b. variant_1_part_b
          c. variant_1_part_c
        **Variant 2**: 
          a. variant_2_part_a
          b. variant_2_part_b
          c. variant_2_part_c

        Utilize these guidelines to generate distinct and engaging questions based on the given context.
    """


    # Define the payload for Ollama
    payload = {
        "messages": [
            {
                "role": "system",
                "content": f"""{base_question_gen}"""
            },
            {
                "role": "user",
                "content": f"""Please generate {n} variants of the question: '{base_question}'""",
            }
        ],
        "stream": False,
        "options": {
            "top_k": 20, 
            "top_p": 0.5, 
            "temperature": 0.5, 
            # "seed": 100, 
        }
    }
    print("Original question" + base_question)
    # Asynchronous call to Ollama API
    response = await asyncio.to_thread(ollama.chat, model='llama3.1', messages=payload['messages'], stream=payload['stream'])
    content = response['message']['content']
    print("Response-" + content)
    variants_dict = extract_variants(base_question, content)
    # Return the response content
    return response['message']['content'], variants_dict


def extract_variants(base_question, content):
    
    variant_pattern = re.compile(r'(\*\*Variant \d+:\*\*.*?)(?=\*\*Variant \d+:|\Z)', re.DOTALL)
    
    # Find all variants
    variants = variant_pattern.findall(content)
    
    # Debug: print found variants
    # print("Found variants:")
    # print(variants)
    
    variant_contents = []
    
    for variant in variants:
        # Remove the variant title and keep only the content
        content_without_title = variant.split('\n', 1)[1].strip()  # Remove the first line (variant title)
        variant_contents.append(content_without_title)  # Store the content in the list
    
    # print  (variant_contents)

    return {base_question: variant_contents}


@app.post("/api/assignments")
async def get_the_assignments(request: CourseIDRequest):
    try:
        course_id, course_name = get_course_info_by_shortname(request.course_shortname)
        
        assignments = get_assignments(course_id)
        return JSONResponse(content={
            "course_name": course_name,
            "course_id": course_id,
            "assignments": assignments
        })
    except HTTPException as e:
        return JSONResponse(content={"error": e.detail}, status_code=e.status_code)
    except Exception as e:
        return JSONResponse(content={"error": str(e)}, status_code=500)



def get_all_courses():
    params = {
        'wstoken': TOKEN,
        'wsfunction': 'core_course_get_courses',
        'moodlewsrestformat': 'json'
    }
    return moodle_api_call(params) 

# Function to get enrolled users in a specific course
def get_enrolled_users(course_id):
    params = {
        'wstoken': TOKEN,
        'wsfunction': 'core_enrol_get_enrolled_users',
        'moodlewsrestformat': 'json',
        'courseid': course_id
    }
    return moodle_api_call(params)

# Function to check admin capabilities
def check_admin_capabilities():
    params = {
        'wstoken': TOKEN,
        'wsfunction': 'core_webservice_get_site_info',
        'moodlewsrestformat': 'json',
    }
    site_info = moodle_api_call(params)
    print("Site Info:", site_info)


def get_course_info_by_shortname(course_shortname):
    params = {
        'wstoken': TOKEN,
        'wsfunction': 'core_course_get_courses_by_field',
        'moodlewsrestformat': 'json',
        'field': 'shortname',
        'value': course_shortname
    }
    result = moodle_api_call(params)
    if result['courses']:
        course = result['courses'][0]
        return course['id'], course['fullname']
    else:
        raise Exception("Course not found")
    
# Function to get assignments for a specific course
def get_assignments(course_id):
    params = {
        'wstoken': TOKEN,
        'wsfunction': 'mod_assign_get_assignments',
        'moodlewsrestformat': 'json',
        'courseids[0]': course_id
    }
    
    extra_params = {'includenotenrolledcourses': 1}
    assignments = moodle_api_call(params, extra_params)
    
    if not assignments.get('courses'):
        print("No courses found.")
        return []

    courses = assignments['courses']
    if not courses:
        print("No courses returned from API.")
        return []

    course_data = courses[0]

    if 'assignments' not in course_data:
        print(f"No assignments found for course: {course_data.get('fullname')}")
        return []

    return course_data['assignments']

# Function to get submissions for a specific assignment
def get_assignment_submissions(assignment_id):
    params = {
        'wstoken': TOKEN,
        'wsfunction': 'mod_assign_get_submissions',
        'moodlewsrestformat': 'json',
        'assignmentids[0]': assignment_id
    }
    submissions = moodle_api_call(params)

    if not submissions.get('assignments'):
        return []

    assignments_data = submissions.get('assignments', [])
    if not assignments_data:
        print("No assignments data returned from API.")
        return []

    assignment_data = assignments_data[0]

    if 'submissions' not in assignment_data:
        print(f"No submissions found for assignment: {assignment_id}")
        return []

    return assignment_data['submissions']

# Function to download a file from a given URL
def download_file(url):
    response = requests.get(url)
    if response.status_code == 200:
        return response.content
    else:
        raise Exception(f"Failed to download file: {response.status_code}, URL: {url}")

# Function to extract text from a PDF file
def extract_text_from_pdf(file_content):
    try:
        doc = fitz.open(stream=file_content, filetype="pdf")
        text = ""
        for page in doc:
            text += page.get_text()
        return text
    except Exception as e:
        return f"Error extracting text from PDF: {str(e)}"

# Function to extract text from a DOCX file
def extract_text_from_docx(file_content):
    with io.BytesIO(file_content) as f:
        doc = Document(f)
        return "\n".join([para.text for para in doc.paragraphs])

# Function to extract text from a TXT file
def extract_text_from_txt(file_content):
    return file_content.decode('utf-8')

# Function to extract text from an image file
def extract_text_from_image(file_content):
    image = Image.open(io.BytesIO(file_content))
    return pytesseract.image_to_string(image)

# Function to extract text from a submission file based on file type
def extract_text_from_submission(file):
    file_url = file['fileurl']
    file_url_with_token = f"{file_url}&token={TOKEN}" if '?' in file_url else f"{file_url}?token={TOKEN}"
    print(f"Downloading file from URL: {file_url_with_token}")  # Log the file URL
    
    file_content = download_file(file_url_with_token)
    file_name = file['filename'].lower()
    print(f"Processing file: {file_name}")  # Log the file name

    try:
        if file_name.endswith('.pdf'):
            return extract_text_from_pdf(file_content)
        elif file_name.endswith('.docx'):
            return extract_text_from_docx(file_content)
        elif file_name.endswith('.txt'):
            return extract_text_from_txt(file_content)
        elif file_name.endswith(('.png', '.jpg', '.jpeg')):
            return extract_text_from_image(file_content)
        else:
            return "Unsupported file format."
    except Exception as e:
        return f"Error extracting text: {str(e)}"

# Function to extract Q&A pairs using regex
def extract_qa_pairs(text):
    qa_pairs = re.findall(r'(Q\d+:\s.*?\nA\d+:\s.*?(?=\nQ\d+:|\Z))', text, re.DOTALL)
    if not qa_pairs:
        return [text.strip()]
    return [pair.strip() for pair in qa_pairs]

# Function to send Q&A pair to grading endpoint and get response
async def process_user_submissions(user, submissions_by_user, activity_type):
    user_id = user['id']
    user_fullname = user['fullname']
    user_email = user['email']
    user_submission = submissions_by_user.get(user_id)
    
    if not user_submission:
        return {
            "Full Name": user_fullname,
            "User ID": user_id,
            "Email": user_email,
            "Total Score": 0,
            "Feedback": "No submission"
        }
    
    total_score = 0
    all_comments = []

    if activity_type == 'assignment':
        for plugin in user_submission['plugins']:
            if plugin['type'] == 'file':
                for filearea in plugin['fileareas']:
                    for file in filearea['files']:
                        try:
                            print(f"\nProcessing file: {file['filename']} for {user_fullname}...")
                            text = extract_text_from_submission(file)
                            qa_pairs = extract_qa_pairs(text)
                            print("QAPAIRS", qa_pairs)
                            for i, qa_pair in enumerate(qa_pairs):
                                try:
                                    # Call the OllamaGA function directly
                                    result = await ollama_aga({"query": qa_pair})
                                    justification = result.get("justification")
                                    avg_score = result.get("average_score")
                                    total_score += avg_score
                                    comment = f"Q{i+1}: {justification}"
                                    all_comments.append(comment)

                                    print(f"  Graded Q{i+1}: Avg. Score = {avg_score:.2f} - {justification}")
                                    
                                except Exception as e:
                                    print(f"  Error grading Q&A pair {i+1} for {user_fullname}: {str(e)}")
                        except Exception as e:
                            print(f"  Error extracting text for {user_fullname}: {str(e)}")

    feedback = " | ".join(all_comments)
    return {
        "Full Name": user_fullname,
        "User ID": user_id,
        "Email": user_email,
        "Total Score": total_score,
        "Feedback": feedback
    }

# Function to get course details by ID
def get_course_by_id(course_id):
    params = {
        'wstoken': TOKEN,
        'wsfunction': 'core_course_get_courses',
        'moodlewsrestformat': 'json',
        'options[ids][0]': course_id
    }
    return moodle_api_call(params)

# Function to write data to a CSV file in Moodle-compatible format
def write_to_csv(data, course_id, assignment_name):
    filename = f"Course_{course_id}_{assignment_name.replace(' ', '_')}_autograded.csv"
    with open(filename, mode='w', newline='', encoding='utf-8') as file:
        writer = csv.writer(file)
        
        writer.writerow(["Full Name", "User ID", "Email", "Total Score", "Feedback"])
        
        for row in data:
            writer.writerow([row["Full Name"], row["User ID"], row["Email"], row["Total Score"], row["Feedback"]])

    print(f"Data successfully written to CSV file: {filename}")

# Function to update a user's grade in Moodle
def update_grade(user_id, assignment_id, grade, feedback):
    params = {
        'wstoken': TOKEN,
        'wsfunction': 'mod_assign_save_grade',
        'moodlewsrestformat': 'json',
        'assignmentid': assignment_id,
        'userid': user_id,
        'grade': grade, 
        'feedback': feedback
    }
    response = moodle_api_call(params)
    print(f"Grade updated for User ID: {user_id}, Status: {response}")

# Main function to integrate with Moodle
async def moodle_integration_pipeline(course_shortname, assignment_name, activity_type):
    try:

        print(f"\n=== Fetching Course Details for Shortname: {course_shortname} ===")
        course_id, course_name = get_course_info_by_shortname(course_shortname)
        print(f"Course ID: {course_id}, Course Name: {course_name}")
        # Fetching course details
        print(f"\n=== Fetching Course Details for Course ID: {course_id} ===")
        course_details = get_course_by_id(course_id)
        if not course_details:
            raise Exception("Course not found.")
        course_name = course_details[0]['fullname']
        print(f"Course Name: {course_name}")

        # Fetching enrolled users
        print("\n=== Fetching Enrolled Users ===")
        users = get_enrolled_users(course_id)
        print(f"Found {len(users)} enrolled users.")

        if activity_type == 'assignment':
            # Fetching assignments
            print("\n=== Fetching Assignments ===")
            activities = get_assignments(course_id)
        else:
            raise Exception("Unsupported activity type.")

        print(f"Found {len(activities)} {activity_type}s.")

        # Matching the activity by name
        activity = next((a for a in activities if a['name'].strip().lower() == assignment_name.strip().lower()), None)
        if not activity:
            raise Exception(f"{activity_type.capitalize()} not found.")

        activity_id = activity['id']
        print(f"{activity_type.capitalize()} '{assignment_name}' found with ID: {activity_id}")

        # Fetching submissions for the assignment
        print("\n=== Fetching Submissions ===")
        submissions = get_assignment_submissions(activity_id)

        print(f"Found {len(submissions)} submissions.")

        submissions_by_user = {s['userid']: s for s in submissions}

        # Processing submissions
        print("\n=== Processing Submissions ===")
        tasks = [process_user_submissions(user, submissions_by_user, activity_type) for user in users]
        processed_data = await asyncio.gather(*tasks)

        # Writing data to CSV
        print("\n=== Writing Data to CSV ===")
        write_to_csv(processed_data, course_id, assignment_name)

        print("\n=== Processing Completed Successfully ===")
        return processed_data

    except Exception as e:
        print(f"\nAn error occurred: {str(e)}")
        raise

@app.post("/api/process")
async def grade_assignment(request: Request):
    data = await request.json()
    course_shortname = data.get("course_shortname")
    assignment_name = data.get("assignment_name")
    activity_type = "assignment"

    try:
        processed_data = await moodle_integration_pipeline(course_shortname, assignment_name, activity_type)
        return JSONResponse(content={"status": "success", "message": "Grading completed successfully", "data": processed_data})
    except Exception as e:
        return JSONResponse(content={"status": "error", "message": str(e)}, status_code=500)



 
@app.post("/api/ollamaAGA")
async def ollama_aga(request: QueryRequest):
    context = await make_request(request.query)
    if context is None:
        raise Exception("Failed to fetch context")
    
    variants, avg_score = await grading_assistant(request.query, context)
    
    response = {
        "justification": variants,
        "average_score": avg_score
    }
    
    return response

@app.post("/api/spandachat")
async def spanda_chat(request: QueryRequest):
    context = await make_request(request.query)
    if context is None:
        raise Exception("Failed to fetch context")
    
    answer = await chatbot(request.query, context)
    
    response = {
        "answer": answer
    }
    
    return response

@app.post("/api/ollamaAQG")
async def ollama_aqg(request: QueryRequestaqg):
    query = request.query
    n = request.NumberOfVariants
    context = await make_request(query)
    variants, variants_dict = await generate_question_variants(query, n, context)
    response = {
        "variants": variants,
        "variants_dict": variants_dict
    }
    return response


@app.post("/api/ollamaAFE")
async def ollama_afe(request: QueryRequest):
    dimensions = {
        # Example structure, fill in with actual dimensions and sub-dimensions
      "Mastery of the Subject": {
            "weight": 0.169,
            "sub-dimensions": {
                "Knowledge of Content and Pedagogy": {
                    "weight": 0.362,
                    "definition": "A deep understanding of their subject matter and the best practices for teaching it.",
                    "example": "In a mathematics course on real analysis, the professor demonstrates best practices by Guiding students through the process of constructing formal proofs step-by-step, highlighting common pitfalls and techniques for overcoming them.",
                    "criteria": {
                        1: "The transcript demonstrates minimal knowledge of content and ineffective pedagogical practices",
                        2: "The transcript demonstrates basic content knowledge but lacks pedagogical skills",
                        3: "The transcript demonstrates adequate content knowledge and uses some effective pedagogical practices",
                        4: "The transcript demonstrates strong content knowledge and consistently uses effective pedagogical practices",
                        5: "The transcript demonstrates exceptional content knowledge and masterfully employs a wide range of pedagogical practices"
                    }
                },
                # "Breadth of Coverage": {
                #     "weight": 0.327,
                #     "definition": "Awareness of different possible perspectives related to the topic taught",
                #     "example": "Teacher discusses different theoretical views, current and prior scientific developments, etc.",
                #     "criteria": {
                #         1: "The transcript shows that the instructor covers minimal content with significant gaps in the curriculum",
                #         2: "The transcript shows that the instructor covers some content but with notable gaps in the curriculum",
                #         3: "The transcript shows that the instructor covers most of the required content with minor gaps",
                #         4: "The transcript shows that the instructor covers all required content thoroughly",
                #         5: "The transcript shows that the instructor covers all required content and provides additional enrichment material"
                #     }
                # },
                "Knowledge of Resources": {
                    "weight": 0.310,
                    "definition": "Awareness of and utilization of a variety of current resources in the subject area to enhance instruction",
                    "example": "The teacher cites recent research studies or books while explaining relevant concepts.",
                    "criteria": {
                        1: "The transcript shows that the instructor demonstrates minimal awareness of resources available for teaching",
                        2: "The transcript shows that the instructor demonstrates limited knowledge of resources and rarely incorporates them",
                        3: "The transcript shows that the instructor demonstrates adequate knowledge of resources and sometimes incorporates them",
                        4: "The transcript shows that the instructor demonstrates strong knowledge of resources and frequently incorporates them",
                        5: "The transcript shows that the instructor demonstrates extensive knowledge of resources and consistently incorporates a wide variety of them"
                    }
                }
            }
        },
        "Expository Quality": {
            "weight": 0.179,
            "sub-dimensions": {
                "Content Clarity": {
                    "weight": 0.266,
                    "definition": "Extent to which the teacher is able to explain the content to promote clarity and ease of understanding.",
                    "example": "Teacher uses simple vocabulary and concise sentences to explain complex concepts.",
                    "criteria": {
                        1: "Does not break down complex concepts, uses confusing, imprecise, and inappropriate language, and does not employ any relevant techniques or integrate them into the lesson flow.",
                        2: "Inconsistently breaks down complex concepts using language that is sometimes confusing or inappropriate, employing few minimally relevant techniques that contribute little to student understanding, struggling to integrate them into the lesson flow.",
                        3: "Generally breaks down complex concepts using simple, precise language and some techniques that are somewhat relevant and contribute to student understanding, integrating them into the lesson flow with occasional inconsistencies.",
                        4: "Frequently breaks down complex concepts using simple, precise language and a variety of relevant, engaging techniques that contribute to student understanding.",
                        5: "Consistently breaks down complex concepts using simple, precise language and a wide variety of highly relevant, engaging techniques such as analogies, examples, visuals, etc. to student understanding, seamlessly integrating them into the lesson flow."
                    }
                },
                # "Demonstrating Flexibility and Responsiveness": {
                #     "weight": 0.248,
                #     "definition": "Ability to adapt to the changing needs of the students in the class while explaining the concepts.",
                #     "example": "The teacher tries to explain a concept using a particular example. On finding that the students are unable to understand, the teacher is able to produce alternate examples or explanation strategies to clarify the concept.",
                #     "criteria": {
                #         1: "Fails to adapt explanations based on student needs and does not provide alternate examples or strategies.",
                #         2: "Rarely adapts explanations, often sticking to the same methods even when students struggle to understand.",
                #         3: "Sometimes adapts explanations and provides alternate examples or strategies, but with limited effectiveness.",
                #         4: "Frequently adapts explanations and offers a variety of alternate examples or strategies that aid student understanding.",
                #         5: "Consistently and seamlessly adapts explanations, providing a wide range of highly effective alternate examples or strategies tailored to student needs."
                #     }
                # },
                "Differentiation Strategies": {
                    "weight": 0.246,
                    "definition": "The methods and approaches used by the teacher to accommodate diverse student needs, backgrounds, learning styles, and abilities.",
                    "example": "During a lesson, the teacher divides the class into small groups based on their readiness levels. She provides more advanced problems for students who grasp the concept quickly, while offering additional support and manipulatives for students who need more help.",
                    "criteria": {
                        1: "Uses no differentiation strategies to meet diverse student needs",
                        2: "Uses minimal differentiation strategies with limited effectiveness",
                        3: "Uses some differentiation strategies with moderate effectiveness",
                        4: "Consistently uses a variety of differentiation strategies effectively",
                        5: "Masterfully employs a wide range of differentiation strategies to meet the needs of all learners"
                    }
                },
                "Communication Clarity": {
                    "weight": 0.238,
                    "definition": "The ability of the teacher to effectively convey information and instructions to students in a clear and understandable manner.",
                    "example": "The teachers voice and language is clear with the use of appropriate voice modulation, tone, and pitch to facilitate ease of understanding.",
                    "criteria": {
                        1: "Communicates poorly with students, leading to confusion and misunderstandings",
                        2: "Communicates with some clarity but often lacks precision or coherence",
                        3: "Communicates clearly most of the time, with occasional lapses in clarity",
                        4: "Consistently communicates clearly and effectively with students",
                        5: "Communicates with exceptional clarity, precision, and coherence, ensuring full understanding"
                    }
                }
            }
        },
        "Class Management": {
            "weight": 0.150,
            "sub-dimensions": {
                "Punctuality": {
                    "weight": 0.261,
                    "definition": "The consistency and timeliness of the teacher's arrival to class sessions, meetings, and other professional obligations.",
                    "example": "The teacher starts and completes live lectures as per the designated time.",
                    "criteria": {
                        1: "Transcripts consistently show late class start times and/or early end times",
                        2: "Transcripts occasionally show late class start times and/or early end times",
                        3: "Transcripts usually show on-time class start and end times",
                        4: "Transcripts consistently show on-time class start and end times",
                        5: "Transcripts always show early class start times and full preparation to begin class on time"
                    }
                },
                "Managing Classroom Routines": {
                    "weight": 0.255,
                    "definition": "The Teacher establishes and maintains efficient routines and procedures to maximize instructional time.",
                    "example": "The teacher starts every session with a recap quiz to remind learners of what was taught earlier. Students prepare for the recap even before the teacher enters the class in a habitual manner.",
                    "criteria": {
                        1: "Classroom routines are poorly managed, leading to confusion and lost instructional time",
                        2: "Classroom routines are somewhat managed but with frequent disruptions",
                        3: "Classroom routines are adequately managed with occasional disruptions",
                        4: "Classroom routines are well-managed, leading to smooth transitions and minimal disruptions",
                        5: "Classroom routines are expertly managed, maximizing instructional time and creating a seamless learning environment"
                    }
                },
                "Managing Student Behavior": {
                    "weight": 0.240,
                    "definition": "The teacher sets clear expectations for behavior and uses effective strategies to prevent and address misbehavior. The teacher encourages student participation and provides fair and equal opportunities to all students in class. The teacher also provides appropriate compliments and feedback to learners’ responses.",
                    "example": "The teacher addresses a students misbehavior in the class in a professional manner and provides constructive feedback using clear guidelines for student behavior expected in the course.",
                    "criteria": {
                        1: "Struggles to manage student behavior, leading to frequent disruptions and an unproductive learning environment. Rarely encourages student participation, with little to no effort to ensure equal opportunities for engagement; provides no or inappropriate feedback and compliments that do not support learning or motivation.",
                        2: "Manages student behavior with limited effectiveness, with some disruptions and off-task behavior. Inconsistently encourages student participation, with unequal opportunities for engagement; provides limited or generic feedback and compliments that minimally support learning and motivation.",
                        3: "Manages student behavior adequately, maintaining a generally productive learning environment. Generally encourages student participation and provides opportunities for engagement, but some students may dominate or be overlooked; provides feedback and compliments, but they may not always be specific or constructive.",
                        4: "Effectively manages student behavior, promoting a positive and productive learning environment. Frequently encourages student participation, provides fair opportunities for engagement, and offers appropriate feedback and compliments that support learning and motivation.",
                        5: "Expertly manages student behavior, fostering a highly respectful, engaged, and self-regulated learning community. Consistently encourages active participation from all students, ensures equal opportunities for engagement, and provides specific, timely, and constructive feedback and compliments that enhance learning and motivation."
                    }
                },
                # "Adherence to Rules": {
                #     "weight": 0.242,
                #     "definition": "The extent to which the teacher follows established rules, procedures, and policies governing classroom conduct and professional behavior.",
                #     "example": "The teacher reminds the students to not circulate cracked versions of a software on the class discussion forum.",
                #     "criteria": {
                #         1: "Consistently disregards or violates school rules and policies",
                #         2: "Occasionally disregards or violates school rules and policies",
                #         3: "Generally adheres to school rules and policies with occasional lapses",
                #         4: "Consistently adheres to school rules and policies",
                #         5: "Strictly adheres to school rules and policies and actively promotes compliance among students"
                #     }
                # }
            }
        },
        "Structuring of Objectives and Content": {
            "weight": 0.168,
            "sub-dimensions": {
                "Organization": {
                    "weight": 0.338,
                    "definition": "The extent to which content is presented in a structured and comprehensive manner with emphasis on important content and proper linking content.",
                    "example": "Teacher starts the class by providing an outline of what all will be covered in that particular class and connects it to previous knowledge of learners.",
                    "criteria": {
                        1: "Transcripts indicate content that is poorly organized, with minimal structure and no clear emphasis on important content. Linking between content is absent or confusing.",
                        2: "Transcripts indicate content that is somewhat organized but lacks a consistent structure and comprehensive coverage. Emphasis on important content is inconsistent, and linking between content is weak",
                        3: "Transcripts indicate content that is adequately organized, with a generally clear structure and comprehensive coverage. Important content is usually emphasized, and linking between content is present.",
                        4: "Transcripts indicate content that is well-organized, with a consistent and clear structure and comprehensive coverage. Important content is consistently emphasized, and linking between content is effective.",
                        5: "Transcripts indicate content that is exceptionally well-organized, with a highly structured, logical, and comprehensive presentation. Important content is strategically emphasized, and linking between content is seamless and enhances learning."
                    }
                },
                "Clarity of Instructional Objectives": {
                    "weight": 0.342,
                    "definition": "The clarity and specificity of the learning objectives communicated to students, guiding the focus and direction of instruction.",
                    "example": "At the start of the lesson, the teacher displays the learning objectives and takes a few moments to explain them to the students.",
                    "criteria": {
                        1: "Content is presented in a confusing or unclear manner",
                        2: "Content is presented with some clarity but with frequent gaps or inconsistencies",
                        3: "Content is presented with adequate clarity, allowing for general understanding",
                        4: "Content is presented with consistent clarity, promoting deep understanding",
                        5: "Content is presented with exceptional clarity, facilitating mastery and transfer of knowledge"
                    }
                },
                "Alignment with the Curriculum": {
                    "weight": 0.319,
                    "definition": "The degree to which the teacher's instructional plans and activities align with the prescribed curriculum objectives and standards.",
                    "example": "The teacher discusses a unit plan that clearly shows the connections between her learning objectives, instructional activities, assessments, and the corresponding curriculum standards.",
                    "criteria": {
                        1: "Instruction is poorly aligned with the curriculum, with significant gaps or deviations",
                        2: "Instruction is somewhat aligned with the curriculum but with frequent inconsistencies",
                        3: "Instruction is generally aligned with the curriculum, covering most required content",
                        4: "Instruction is consistently aligned with the curriculum, covering all required content",
                        5: "Instruction is perfectly aligned with the curriculum, covering all required content and providing meaningful extensions"
                    }
                }
            }
        },
        "Qualities of Interaction": {
            "weight": 0.168,
            "sub-dimensions": {
                "Instructor Enthusiasm And Positive demeanor": {
                    "weight": 0.546,
                    "definition": "Extent to which a teacher is enthusiastic and committed to making the course interesting, active, dynamic, humorous, etc.",
                    "example": "Teacher uses an interesting fact or joke to engage the class.",
                    "criteria": {
                        1: "Instructor exhibits a negative or indifferent demeanor and lacks enthusiasm for teaching",
                        2: "Instructor exhibits a neutral demeanor and occasional enthusiasm for teaching",
                        3: "Instructor exhibits a generally positive demeanor and moderate enthusiasm for teaching",
                        4: "Instructor exhibits a consistently positive demeanor and strong enthusiasm for teaching",
                        5: "Instructor exhibits an exceptionally positive demeanor and infectious enthusiasm for teaching, inspiring student engagement"
                    }
                },
                "Individual Rapport": {
                    "weight": 0.453,
                    "definition": "Extent to which the teacher develops a rapport with individual students and their concerns during and beyond class hours. The teacher provides assistance, guidance, and resources to help students overcome obstacles, address challenges, and achieve success.",
                    "example": "Teacher shows an interest in student concerns, and attempts to resolve individual queries both during the class and through forums/individual communication.",
                    "criteria": {
                        1: "Minimal or negative rapport with individual students interactions",
                        2: "Limited rapport with individual students, with infrequent personalized",
                        3: "Adequate rapport with individual students, with some personalized interactions",
                        4: "Strong rapport with individual students, with frequent personalized interactions and support",
                        5: "Exceptional rapport with each individual student, with highly personalized interactions, support, and guidance"
                    }
                }
            }
        },
        "Evaluation of Learning": {
            "weight": 0.163,
            "sub-dimensions": {
                "Course Level Assessment": {
                    "weight": 0.333,
                    "definition": "The course level assessment is in line with the curriculum of the course and effectively checks whether the course outcomes are being met.",
                    "example": "The teacher selects or uses test items that reflect the course outcome.",
                    "criteria": {
                        1: "The course level assessment is not aligned with the curriculum, does not cover course outcomes, and uses methods that are ineffective in measuring student achievement of these outcomes.",
                        2: "The course level assessment is poorly aligned with the curriculum, covers few course outcomes, and uses methods that are limited in their ability to effectively measure student achievement of these outcomes.",
                        3: "The course level assessment is generally aligned with the curriculum, covers some course outcomes, and uses methods that adequately measure student achievement of these outcomes, but may have minor gaps or inconsistencies.",
                        4: "The course level assessment is well-aligned with the curriculum, covers most course outcomes, and uses appropriate methods to effectively measure student achievement of these outcomes.",
                        5: "The course level assessment is perfectly aligned with the curriculum, comprehensively covers all course outcomes, and employs highly effective methods to accurately measure student achievement of these outcomes."
                    }
                },
                "Clear Grading Criteria": {
                    "weight": 0.333,
                    "definition": "The teacher uses a clear and structured rubric which is communicated to the learners prior to any evaluation. The teacher is not biased in their assessment of learner performance.",
                    "example": "The teacher discusses the assessment rubric by providing examples of good responses and the criteria for grading with the learners before conducting any tests.",
                    "criteria": {
                        1: "The teacher rarely or never uses a rubric, does not communicate assessment criteria to learners before evaluation, and the teacher's assessment is highly biased and unfair.",
                        2: "The teacher inconsistently uses a rubric that is poorly structured or not clearly communicated to learners before evaluation, and the teacher's assessment may be noticeably biased at times.",
                        3: "The teacher generally uses a rubric that is communicated to learners before evaluation, but the rubric may lack some clarity or structure, and the teacher's assessment may occasionally show minor bias.",
                        4: "The teacher frequently uses a clear and structured rubric that is communicated to learners prior to evaluation, and applies the rubric fairly to all learners with minimal bias.",
                        5: "The teacher consistently uses a well-defined, comprehensive rubric that is clearly communicated to learners well in advance of any evaluation, and applies the rubric objectively and fairly to all learners without any bias."
                    }
                },
                "Assignments/readings": {
                    "weight": 0.332,
                    "definition": "The teacher provides assignments/homework/literature which is relevant and contributes to a deeper understanding of the topics taught to track the progress of learners. The teacher also discusses the solutions and feedback based on previously assigned homework/assignment.",
                    "example": "The teacher provides clear instructions on the homework task that the students are given and how it is relevant to the topics taught in class. In the following class, the teacher discusses the answers and any common mistakes made by learners.",
                    "criteria": {
                        1: "The teacher rarely or never provides relevant assignments, homework, or literature that contribute to understanding the topics taught, does not track learner progress, and fails to discuss solutions and feedback based on previous work.",
                        2: "The teacher inconsistently provides assignments, homework, and literature that are minimally relevant and contribute little to understanding the topics taught, rarely tracks learner progress, and seldom discusses solutions and feedback based on previous work.",
                        3: "The teacher generally provides assignments, homework, and literature that are somewhat relevant and contribute to understanding the topics taught, occasionally tracks learner progress, and sometimes discusses solutions and feedback based on previous work.",
                        4: "The teacher frequently provides relevant assignments, homework, and literature that contribute to a deeper understanding of the topics taught, tracks learner progress, and discusses solutions and feedback based on previously assigned work.",
                        5: "The teacher consistently provides highly relevant and challenging assignments, homework, and literature that significantly deepen learners' understanding of the topics taught, regularly tracks learner progress, and engages in thorough discussions of solutions and feedback based on previous work."
                    }
                }
            }
        }
    
    }

    instructor_name = request.query
    dimension_scores = {}
    all_responses = {}

    for dimension, dimension_data in dimensions.items():
        sub_dimensions = dimension_data["sub-dimensions"]
        total_sub_weight = sum([sub_data["weight"] for sub_data in sub_dimensions.values()])
        weighted_sub_scores = 0
        
        all_responses[dimension] = {}

        for sub_dim_name, sub_dim_data in sub_dimensions.items():
            query = f"""
            Evaluate the {sub_dim_name.lower()} of the instructor "{instructor_name}" based on the following criteria:
            Definition: {sub_dim_data['definition']}
            Example: {sub_dim_data['example']}
            Criteria:
            {json.dumps(sub_dim_data['criteria'], indent=4)}
            Provide a score between 1 and 5 based on the criteria.
            """

            # Assuming make_request returns the context required for evaluation
            context = await make_request(instructor_name)
            response, score_dict = await instructor_eval(instructor_name, context, sub_dim_name, sub_dim_data["criteria"])

            # Store the response and score
            all_responses[dimension][sub_dim_name] = f"{sub_dim_name}: {score_dict.get(sub_dim_name, 'N/A')} - Detailed Explanation with Examples and justification for examples.\n\n{response}"
            score_str = score_dict.get(sub_dim_name, "0")
            score = int(score_str) if score_str.isdigit() else 0
            normalized_score = (score / 5) * sub_dim_data["weight"]
            weighted_sub_scores += normalized_score

        # Calculate the weighted average for this dimension
        dimension_score = (weighted_sub_scores / total_sub_weight) * dimension_data["weight"]
        dimension_scores[dimension] = dimension_score

    return {
        "dimension_scores": dimension_scores,
        "DOCUMENT": all_responses
    }
# Modified import endpoint to handle transcript uploads
@app.post("/api/importTranscript")
async def import_transcript(transcript_data: UploadFile = File(...)):
    try:
        contents = await transcript_data.file.read()

        # Convert to Base64
        base64_content = base64.b64encode(contents).decode('utf-8')

        # Upload to Weaviate using the existing endpoint
        upload_to_weaviate(base64_content, transcript_data.filename)

        return JSONResponse(content={"message": "Transcript uploaded successfully"})
    except ValidationError as e:
        # Handle validation errors
        return JSONResponse(content={"error": e.errors()}, status_code=422)
    except HTTPException as e:
        raise e  # Reraise the exception if it's a Weaviate import failure
    except Exception as e:
        return JSONResponse(content={"error": str(e)}, status_code=500)


@app.post("/api/upload_transcript")
async def upload_transcript(payload: ImportPayload):
    try:
        for file_data in payload.data:
            file_content = base64.b64decode(file_data.content)
            with open(file_data.filename, "wb") as file:
                file.write(file_content)
        
        logging = []

        print(f"Received payload: {payload}")
        if production:
            logging.append(
                {"type": "ERROR", "message": "Can't import when in production mode"}
            )
            return JSONResponse(
                content={
                    "logging": logging,
                }
            )

        try:
            set_config(manager, payload.config)
            documents, logging = manager.import_data(
                payload.data, payload.textValues, logging
            )

            return JSONResponse(
                content={
                    "logging": logging,
                }
            )

        except Exception as e:
            logging.append({"type": "ERROR", "message": str(e)})
            return JSONResponse(
                content={
                    "logging": logging,
                }
            )


    except Exception as e:
        print(f"Error during import: {e}")
        raise HTTPException(status_code=500, detail="Error processing the file")
    
@app.post("/api/evaluate_Transcipt")
async def evaluate_Transcipt(request: QueryRequest):
    dimensions = {
        "Communication Clarity": "The ability to convey information and instructions clearly and effectively so that students can easily understand the material being taught.\n"
                                "0: Instructions are often vague or confusing, leading to frequent misunderstandings among students.\n"
                                "Example: 'Read the text and do the thing.'\n"
                                "1: Occasionally provides clear instructions but often lacks detail, requiring students to ask for further clarification.\n"
                                "Example: 'Read the chapter and summarize it.'\n"
                                "2: Generally clear and detailed in communication, though sometimes slightly ambiguous.\n"
                                "Example: 'Read chapter 3 and summarize the main points in 200 words.'\n"
                                "3: Always communicates instructions and information clearly, precisely, and comprehensively, ensuring students fully understand what is expected.\n"
                                "Example: 'Read chapter 3, identify the main points, and write a 200-word summary. Make sure to include at least three key arguments presented by the author.'",

        "Punctuality": "Consistently starting and ending classes on time, as well as meeting deadlines for assignments and other class-related activities.\n"
                    "0: Frequently starts and ends classes late, often misses deadlines for assignments and class-related activities.\n"
                    "Example: Class is supposed to start at 9:00 AM but often begins at 9:15 AM, and assignments are returned late.\n"
                    "1: Occasionally late to start or end classes and sometimes misses deadlines.\n"
                    "Example: Class sometimes starts a few minutes late, and assignments are occasionally returned a day late.\n"
                    "2: Generally punctual with minor exceptions, mostly meets deadlines.\n"
                    "Example: Class starts on time 90%' of the time, and assignments are returned on the due date.\n"
                    "3: Always starts and ends classes on time, consistently meets deadlines for assignments and other activities.\n"
                    "Example: Class starts exactly at 9:00 AM every day, and assignments are always returned on the specified due date.",

        "Positivity": "Maintaining a positive attitude, providing encouragement, and fostering a supportive and optimistic learning environment.\n"
                    "0: Rarely displays a positive attitude, often appears disengaged or discouraging.\n"
                    "Example: Rarely smiles or offers encouragement, responds negatively to student questions.\n"
                    "1: Occasionally positive, but can be inconsistent in attitude and support.\n"
                    "Example: Sometimes offers praise but often seems indifferent.\n"
                    "2: Generally maintains a positive attitude and provides encouragement, though with occasional lapses.\n"
                    "Example: Usually offers praise and support but has off days.\n"
                    "3: Consistently maintains a positive and encouraging attitude, always fostering a supportive and optimistic environment.\n"
                    "Example: Always greets students warmly, frequently provides positive feedback and encouragement.",

    }


    instructor_name = request.query

    all_responses = {}
    all_scores = {}

    for dimension, explanation in dimensions.items():
        query = f"Judge document name {instructor_name} based on {dimension}."
        context = await make_request(query)  # Assuming make_request is defined elsewhere to get the context
        # print(f"CONTEXT for {dimension}:")
        # print(context)  # Print the context generated
        result_responses, result_scores = await instructor_eval(instructor_name, context, dimension, explanation)
        print(result_responses)
        print(result_scores)
        # Extract only the message['content'] part and store it
        all_responses[dimension] = result_responses[dimension]['message']['content']
        all_scores[dimension] = result_scores[dimension]
    
    print("SCORES:")
    print(json.dumps(all_scores, indent=2))
    response = {
        "DOCUMENT": all_responses,
        "SCORES": all_scores
    }
    
    return response

async def resume_eval(resume_name, jd_name, context, score_criterion, explanation):
    user_context = "".join(context)
    responses = {}
    scores_dict = {}

    evaluate_instructions = f"""
        [INST]
        -Instructions:
            You are tasked with evaluating a resume named {resume_name} in comparison to a job description named {jd_name} based on the criterion: {score_criterion} - {explanation}.

        -Evaluation Details:
            -Focus exclusively on the provided resume and job description.
            -Assign scores from 0 to 3:
                0: Poor performance
                1: Average performance
                2: Good performance
                3: Exceptional performance
        -Criteria:
            -Criterion Explanation: {explanation}
            -If the resume and job description lack sufficient information to judge {score_criterion}, mark it as N/A and provide a clear explanation.
            -Justify any score that is not a perfect 3.

        Strictly follow the output format-
        -Output Format:
            -{score_criterion}: Score: score(range of 0 to 3, or N/A)

            -Detailed Explanation with Examples and justification for examples:
                -Example 1: "[Quoted text from resume/job description]" [Description]
                -Example 2: "[Quoted text from resume/job description]" [Description]
                -Example 3: "[Quoted text from resume/job description]" [Description]
                -...
                -Example n: "[Quoted text from resume/job description]" [Description]
            -Include both positive and negative instances.
            -Highlight poor examples if the score is not ideal.

            -Consider the context surrounding the example statements, as the context in which a statement is made is extremely important.

            Rate strictly on a scale of 0 to 3 using whole numbers only.

            Ensure the examples are directly relevant to the evaluation criterion and discard any irrelevant excerpts.
        [/INST]
    """
    system_message = """This is a chat between a user and a judge. The judge gives helpful, detailed, and polite suggestions for improvement for a candidate's resume based on the given context - the context contains resumes and job descriptions. The assistant should also indicate when the judgement be found in the context."""

    formatted_context = f"""Here are given documents:
                    [RESUME START]
                    {user_context}
                    [RESUME END]
                    [JOB DESCRIPTION START]
                    {user_context}
                    [JOB DESCRIPTION END]"""

    user_prompt = f"""Please provide an evaluation of the resume named '{resume_name}' in comparison to the job description named '{jd_name}' on the following criteria: '{score_criterion}'. Only include information from the provided documents."""

    payload = {
        "messages": [
            {
                "role": "system",
                "content": system_message
            },
            {
                "role": "user",
                "content": formatted_context + "/n/n" + evaluate_instructions + "/n/n" + user_prompt + " Strictly follow the format of output provided."
            }
        ],
        "stream": False,
        "options": {
            "top_k": 1,
            "top_p": 1,
            "temperature": 0,
            "seed": 100
        }
    }

    response = await asyncio.to_thread(ollama.chat, model='llama3.1', messages=payload['messages'], stream=payload['stream'])
    responses[score_criterion] = response
    content = response['message']['content']

    pattern = rf'(score:\s*([\s\S]*?)(\d+)|\**{score_criterion}\**\s*:\s*(\d+))'
    match = re.search(pattern, content, re.IGNORECASE)

    if match:
        if match.group(3):
            score_value = match.group(3).strip()
        elif match.group(4):
            score_value = match.group(4).strip()
        else:
            score_value = "N/A"
        scores_dict[score_criterion] = score_value
    else:
        scores_dict[score_criterion] = "N/A"

    return responses, scores_dict


# Define the extract_score function
def extract_score(response_content):
    # Regular expression to find the score in the response
    score_match = re.search(r'Score:\s*(\d+|N/A)', response_content)
    if score_match:
        score = score_match.group(1)
        if score == 'N/A':
            return score
        return int(score)
    return None

async def resume_eval(resume_name, jd_name, context, score_criterion, explanation):
    user_context = "".join(context)
    responses = {}
    scores_dict = {}

    evaluate_instructions = f"""
        [INST]
        -Instructions:
            You are tasked with evaluating a candidate's resume in comparison to a job description based on the criterion: {score_criterion} - {explanation}.

        -Evaluation Details:
            -Focus exclusively on the provided resume and job description.
            -Assign scores from 0 to 3:
                0: Poor performance
                1: Average performance
                2: Good performance
                3: Exceptional performance
        -Criteria:
            -Criterion Explanation: {explanation}
            -If the resume lacks sufficient information to judge {score_criterion}, mark it as N/A and provide a clear explanation.
            -Justify any score that is not a perfect 3.

        Strictly follow the output format-
        -Output Format:
            -{score_criterion}: Score: score(range of 0 to 3, or N/A)

            -Detailed Explanation with Examples and justification for examples:
                -Example 1: "[Quoted text from resume or job description]" [Description]
                -Example 2: "[Quoted text from resume or job description]" [Description]
                -Example 3: "[Quoted text from resume or job description]" [Description]
                -...
                -Example n: "[Quoted text from resume or job description]" [Description]
            -Include both positive and negative instances.
            -Highlight poor examples if the score is not ideal.

            -Consider the context surrounding the example statements, as the context in which a statement is made is extremely important.

            Rate strictly on a scale of 0 to 3 using whole numbers only.

            Ensure the examples are directly relevant to the evaluation criterion and discard any irrelevant excerpts.
        [/INST]
    """
    system_message = """This is a chat between a user and a judge. The judge gives helpful, detailed, and polite suggestions for improvement for a particular candidate from the given context - the context contains resumes and job descriptions. The assistant should also indicate when the judgment is found in the context."""
    
    formatted_documents = f"""Here are the given documents for {resume_name} and {jd_name}:
                    [RESUME START]
                    {user_context}
                    [RESUME END]
                    [JOB DESCRIPTION START]
                    {user_context}
                    [JOB DESCRIPTION END]"""
    
    user_prompt = f"""Please provide an evaluation of the candidate named '{resume_name}' in comparison to the job description named '{jd_name}' on the following criteria: '{score_criterion}'. Only include information from the resume and job description where '{resume_name}' is the candidate."""

    payload = {
        "messages": [
            {
                "role": "system",
                "content": system_message
            },
            {
                "role": "user",
                "content": formatted_documents + "\n\n" + evaluate_instructions + "\n\n" + user_prompt
            }
        ],
        "stream": False  # Assuming that streaming is set to False, adjust based on your implementation
    }

    eval_response = await asyncio.to_thread(ollama.chat, model='llama3.1', messages=payload['messages'])  # Assuming chat function is defined to handle the completion request

    # Log the eval_response to see its structure
    print("eval_response:", eval_response)
    
    try:
        eval_response_content = eval_response['message']['content']
    except KeyError as e:
        raise KeyError(f"Expected key 'message' not found in response: {eval_response}")

    response = {
        score_criterion: {
            "message": {
                "content": eval_response_content
            }
        }
    }
    score = extract_score(eval_response_content)
    scores_dict[score_criterion] = score

    return response, scores_dict



class QueryRequest(BaseModel):
    query: List[str]

@app.post("/api/evaluate_Resume")
async def evaluate_Resume(request: QueryRequest):
    if len(request.query) != 2:
        raise HTTPException(status_code=400, detail="Invalid request format. Expected two items in query list.")
    
    resume_name, jd_name = request.query
    dimensions = {
        "Qualification Match": "The extent to which the candidate's educational background, certifications, and experience align with the specific requirements outlined in the job description.\n"
            "0: Qualifications are largely unrelated to the position.\n"
            "Example: The job requires a Master's degree in Computer Science, but the candidate has a Bachelor's in History.\n"
            "1: Some relevant qualifications but significant gaps exist.\n"
            "Example: The candidate has a Bachelor's in Computer Science but lacks the required 3 years of industry experience.\n"
            "2: Mostly meets the qualifications with minor gaps.\n"
            "Example: The candidate meets most qualifications but lacks experience with a specific programming language mentioned in the job description.\n"
            "3: Exceeds qualifications, demonstrating additional relevant skills or experience.\n"
            "Example: The candidate exceeds the required experience and has additional certifications in relevant areas.",
        "Experience Relevance": "The degree to which the candidate's prior teaching, research, or industry experience is relevant to the courses they would be teaching.\n"
            "0: Little to no relevant experience in the subject matter.\n"
            "Example: The candidate has no prior experience teaching or working with the programming languages listed in the course syllabus.\n"
            "1: Some relevant experience but mostly in unrelated areas.\n"
            "Example: The candidate has experience in web development but the course focuses on mobile app development.\n"
            "2: Solid experience in related fields but limited direct experience in the specific subject.\n"
            "Example: The candidate has taught general computer science courses but not the specific advanced algorithms course they are applying for.\n"
            "3: Extensive experience directly teaching or working in the subject area.\n"
            "Example: The candidate has 5+ years of experience teaching the specific course they are applying for and has published research in the field.",
        "Skillset Alignment": "How well the candidate's demonstrated skills (e.g., technical skills, communication, leadership) match the required competencies for the role.\n"
            "0: Skills are largely misaligned with the job requirements.\n"
            "Example: The job requires strong communication and presentation skills, but the candidate has no experience presenting or leading workshops.\n"
            "1: Possesses some required skills but lacks others.\n"
            "Example: The candidate has strong technical skills but lacks experience with collaborative project management tools.\n"
            "2: Demonstrates most of the required skills with some room for improvement.\n"
            "Example: The candidate has good communication skills but could benefit from additional training in public speaking.\n"
            "3: Possesses all required skills and demonstrates advanced abilities in some areas.\n"
            "Example: The candidate has excellent technical skills, is a highly effective communicator, and has a proven track record of mentoring junior developers.",
        "Potential Impact": "An assessment of the candidate's potential to contribute positively to the department and the institution as a whole, based on their resume and cover letter.\n"
            "0: Unclear or negative potential impact based on application materials.\n"
            "Example: The candidate's application materials are vague and do not highlight any specific contributions they could make.\n"
            "1: Potential for minimal impact or contribution.\n"
            "Example: The candidate's resume shows basic qualifications but no indication of going above and beyond.\n"
            "2: Demonstrates potential for moderate positive impact.\n"
            "Example: The candidate has experience with relevant projects and expresses enthusiasm for contributing to the department's research initiatives.\n"
            "3: Shows strong potential to significantly impact the department and institution through teaching, research, or other activities.\n"
            "Example: The candidate has a strong publication record, outstanding references, and a clear vision for how they would enhance the curriculum.",
        "Overall Fit": "A holistic assessment of how well the candidate aligns with the department's culture, values, and long-term goals.\n"
            "0: Poor overall fit with the department.\n"
            "Example: The candidate's values and goals conflict with the department's focus on collaborative learning.\n"
            "1: Some alignment but significant differences in values or goals.\n"
            "Example: The candidate is passionate about research but the department prioritizes teaching excellence.\n"
            "2: Good fit with some areas of potential misalignment.\n"
            "Example: The candidate aligns well with most of the department's values but has a different teaching style than is typical for the institution.\n"
            "3: Excellent fit with the department's culture, values, and goals.\n"
            "Example: The candidate's teaching philosophy, research interests, and collaborative spirit perfectly complement the department's existing strengths and future aspirations."
    }

    all_responses = {}
    all_scores = {}

    for dimension, explanation in dimensions.items():
        query = f"Judge Resume named {resume_name} in comparison to Job Description named {jd_name} based on {dimension}."
        context = await make_request(query)  # Assuming make_request is defined elsewhere to get the context
        result_responses, result_scores = await resume_eval(resume_name, jd_name, context, dimension, explanation)
        all_responses[dimension] = result_responses[dimension]['message']['content']
        all_scores[dimension] = result_scores[dimension]
    
    response = {
        "DOCUMENT": all_responses,
        "SCORES": all_scores
    }
    
    return response