import pandas as pd
import streamlit as st
import os
import fitz
import tempfile
from langchain.chains import RetrievalQA
import io
from streamlit_pdf_viewer import pdf_viewer
import json
from langchain_community.document_loaders import PyPDFLoader
from langchain_community.vectorstores import FAISS
from langchain_core.prompts import PromptTemplate
from langchain_groq import ChatGroq
from langchain_text_splitters import CharacterTextSplitter
from langchain_huggingface import HuggingFaceEmbeddings
from dotenv import load_dotenv

load_dotenv()

# Set page config
st.set_page_config(page_title="📚 ChatPDF", layout="wide")

# Initialize session state variables
if "current_page" not in st.session_state:
    st.session_state.current_page = 0
if "zoom_level" not in st.session_state:
    st.session_state.zoom_level = 1.0
if "sources" not in st.session_state:
    st.session_state.sources = []
if "doc" not in st.session_state:
    st.session_state.doc = None

# Sidebar for PDF upload and display
with st.sidebar:
    st.title("📤 Upload PDF")
    uploaded_file = st.file_uploader("Choose a PDF file", type="pdf")
    
    if uploaded_file is not None:
        st.success("PDF uploaded successfully!")
        
        # Display PDF in sidebar with zoom control
        with st.expander("Preview PDF", expanded=True):
            st.session_state.zoom_level = st.slider("Zoom", 0.5, 2.0, st.session_state.zoom_level, 0.1)
            pdf_bytes = uploaded_file.getvalue()
            pdf_display = pdf_viewer(
                pdf_bytes, 
                width=int(300 * st.session_state.zoom_level),
                height=int(500 * st.session_state.zoom_level)
            )

# Main content
st.title("📚 ChatPDF")
st.subheader("Chat with your PDF documents")

# Custom function to extract document objects from uploaded file
def extract_documents_from_file(uploaded_file):
    with tempfile.NamedTemporaryFile(delete=False) as temp_file:
        temp_file.write(uploaded_file.getvalue())
        
    loader = PyPDFLoader(temp_file.name)
    documents = loader.load()
    
    os.unlink(temp_file.name)
    return documents

def locate_pages_containing_excerpts(document, excerpts):
    relevant_pages = []
    for page_num in range(len(document)):
        page = document.load_page(page_num)
        if any(excerpt and page.search_for(excerpt) for excerpt in excerpts):
            relevant_pages.append(page_num)
    return relevant_pages if relevant_pages else [0]

@st.cache_resource
def initialize_language_model():
    return ChatGroq(
        temperature=0,
        groq_api_key=os.getenv("GROQ_API_KEY"),
        model_name="mixtral-8x7b-32768"
    )

@st.cache_resource
def get_embeddings():
    embeddings = HuggingFaceEmbeddings(
        model_name="all-MiniLM-L6-v2"
    )
    return embeddings

@st.cache_resource
def setup_qa_system(_documents):
    try:
        text_splitter = CharacterTextSplitter(chunk_size=512, chunk_overlap=0)
        text_chunks = text_splitter.split_documents(_documents)

        vector_store = FAISS.from_documents(text_chunks, get_embeddings())
        retriever = vector_store.as_retriever(
            search_type="mmr", search_kwargs={"k": 2, "fetch_k": 4}
        )

        return RetrievalQA.from_chain_type(
            initialize_language_model(),
            chain_type="stuff",
            retriever=retriever,
            return_source_documents=True,
            chain_type_kwargs={"prompt": CUSTOM_PROMPT},
        )
    except Exception as e:
        st.error(f"Error setting up QA system: {str(e)}")
        return None

def generate_highlight_annotations(document, excerpts):
    annotations = []
    if document and excerpts:
        for page_num, page in enumerate(document):
            for excerpt in excerpts:
                if excerpt:
                    for inst in page.search_for(excerpt):
                        annotations.append({
                            "page": page_num + 1,
                            "x": inst.x0,
                            "y": inst.y0,
                            "width": inst.x1 - inst.x0,
                            "height": inst.y1 - inst.y0,
                            "color": "yellow",
                        })
    return annotations

CUSTOM_PROMPT_TEMPLATE = """
Use the following pieces of context to answer the user question. If you
don't know the answer, just say that you don't know, don't try to make up an
answer.

{context}

Question: {question}

Please provide your answer in the following JSON format: 
{{
    "answer": "Your detailed answer here",
    "sources": "Direct sentences or paragraphs from the context that support 
        your answers. ONLY RELEVANT TEXT DIRECTLY FROM THE DOCUMENTS. DO NOT 
        ADD ANYTHING EXTRA. DO NOT INVENT ANYTHING."
}}

The JSON must be a valid json format and can be read with json.loads() in
Python. Answer:
"""

CUSTOM_PROMPT = PromptTemplate(
    template=CUSTOM_PROMPT_TEMPLATE, input_variables=["context", "question"]
)

# Main chat interface
if uploaded_file is not None:
    file = uploaded_file.getvalue()

    with st.spinner("Processing file..."):
        documents = extract_documents_from_file(uploaded_file)
        st.session_state.doc = fitz.open(stream=io.BytesIO(file), filetype="pdf")

    if documents:
        with st.spinner("Setting up QA system..."):
            qa_system = setup_qa_system(documents)
            if qa_system is None:
                st.error("Failed to set up QA system. Please check if the language model is available and try again.")
            else:
                st.success("QA system ready!")

        if "chat_history" not in st.session_state:
            st.session_state.chat_history = [
                {"role": "assistant", "content": "Hello! I'm ready to answer questions about your PDF. What would you like to know?"}
            ]

        # Display chat history
        for msg in st.session_state.chat_history:
            with st.chat_message(msg["role"]):
                st.write(msg["content"])

        # User input
        user_input = st.chat_input("Ask a question about your PDF...")

        if user_input:
            st.session_state.chat_history.append({"role": "user", "content": user_input})
            with st.chat_message("user"):
                st.write(user_input)

            with st.spinner("Generating response..."):
                try:
                    result = qa_system.invoke({"query": user_input})
                    parsed_result = json.loads(result['result'])

                    answer = parsed_result['answer']
                    sources = parsed_result['sources']

                    sources = sources.split(". ") if pd.notna(sources) else []

                    st.session_state.chat_history.append({"role": "assistant", "content": answer})
                    with st.chat_message("assistant"):
                        st.write(answer)

                    # Update the session state with new sources
                    st.session_state.sources = sources

                    # Set a flag to indicate chat interaction has occurred
                    st.session_state.chat_occurred = True

                except json.JSONDecodeError:
                    st.error("There was an error parsing the response. Please try again.")
                except Exception as e:
                    st.error(f"An error occurred: {str(e)}")

        # Highlight PDF excerpts
        if st.session_state.doc and st.session_state.get("chat_occurred", False):
            doc = st.session_state.doc
            st.session_state.total_pages = len(doc)

            # PDF display section
            st.subheader("PDF Preview with Highlighted Excerpts")

            # Navigation controls
            col1, col2, col3 = st.columns([1, 3, 1])
            with col1:
                if st.button("⬅️ Previous") and st.session_state.current_page > 0:
                    st.session_state.current_page -= 1
            with col2:
                st.session_state.current_page = st.number_input(
                    "Page", min_value=1, max_value=st.session_state.total_pages, 
                    value=st.session_state.current_page + 1
                ) - 1
            with col3:
                if st.button("Next ➡️") and st.session_state.current_page < st.session_state.total_pages - 1:
                    st.session_state.current_page += 1

            # Get annotations with correct coordinates
            annotations = generate_highlight_annotations(doc, st.session_state.sources)

            # Display the PDF viewer
            pdf_viewer(
                file,
                width=700,
                height=800,
                annotations=annotations,
                pages_to_render=[st.session_state.current_page + 1],
            )

else:
    st.info("Please upload a PDF file in the sidebar to get started.")